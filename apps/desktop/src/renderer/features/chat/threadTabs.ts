/**
 * Thread tabs (multi-agent) — pure routing + persistence helpers (#1035).
 *
 * A chat send is routed by a *routing key*: the base session key while the
 * main tab is selected, `desktop:<threadId>` while a sub-thread tab is
 * (ChatConsole.handleSend).  Both the per-send listeners and the crash-recovery
 * listeners must filter events on that same key, so the computation lives here
 * — one definition, unit-tested — instead of being duplicated at the call
 * sites.
 *
 * The selected tab (and the tab list itself) is persisted per session in
 * sessionStorage: a renderer crash reloads the page, and without the persisted
 * pair the UI would silently fall back to the main tab while the backend turn
 * it was watching keeps streaming under `desktop:<threadId>`.
 *
 * sessionStorage (not localStorage) is deliberate — the scope is one app run.
 * Tabs describe live sub-agents of the running backend; reopening the app later
 * must not resurrect them.
 */

export interface ThreadTab {
  threadId: string;
  agentType: string;
  label: string;
}

export interface ThreadTabsState {
  tabs: ThreadTab[];
  /** threadId of the selected tab; always present in `tabs`. */
  active: string;
}

export const MAIN_THREAD_ID = 'main';

export const MAIN_THREAD_TAB: ThreadTab = {
  threadId: MAIN_THREAD_ID,
  agentType: 'main',
  label: '主线程',
};

/** Per-session storage keys (the session key is part of the key: tabs are
 *  never shared across sessions). */
export const ACTIVE_THREAD_STORAGE_PREFIX = 'miqi-active-thread:';
export const THREAD_TABS_STORAGE_PREFIX = 'miqi-thread-tabs:';

export function activeThreadStorageKey(sessionKey: string): string {
  return `${ACTIVE_THREAD_STORAGE_PREFIX}${sessionKey}`;
}

export function threadTabsStorageKey(sessionKey: string): string {
  return `${THREAD_TABS_STORAGE_PREFIX}${sessionKey}`;
}

/** The routing key a send from `threadId` passes to chat.send. */
export function routingKeyFor(sessionKey: string, threadId: string): string {
  return threadId === MAIN_THREAD_ID ? sessionKey : `desktop:${threadId}`;
}

/**
 * Whether an event tagged `eventSessionKey` belongs to the view the user is on
 * (base session `sessionKey`, selected tab `threadId`).
 *
 * Only the SELECTED tab's routing key counts (#1035 复审 P1): the base session
 * on the main tab, `desktop:<threadId>` on a sub-thread tab.  The other tab of
 * the same session is now rejected too — its events belong to a DIFFERENT
 * turn, and one listener carries one set of turn-scoped state (latched turn
 * id, reasoning buffer, `streaming`), so adopting a second key lets two
 * concurrent turns of one session interleave into it (two turns' reasoning
 * fused into one thinking block; the first terminal latching the turn id and
 * the second, differently-tagged one being dropped → a turn that never
 * settles).  A turn on a tab the user is not on is left to the normal history
 * / cache path once they switch to it.
 *
 * Untagged (legacy) events carry no key to compare and stay this session's,
 * as before.
 */
export function isEventForView(
  eventSessionKey: string | undefined,
  sessionKey: string,
  threadId: string
): boolean {
  if (!eventSessionKey) return true;
  return eventSessionKey === routingKeyFor(sessionKey, threadId);
}

/**
 * The full adoption decision for one recovered crash-recovery event (#1035).
 *
 * Two independent gates, and both must pass:
 *
 *  1. The event must belong to the VIEW the user is on — exactly the selected
 *     tab's routing key, nothing else (`isEventForView`). This is what lets a
 *     thread-scoped turn (`desktop:<threadId>`) be resumed at all; the events
 *     of any other session/thread — including the other tab of this same
 *     session — are rejected, so concurrent turns cannot share (and corrupt)
 *     the one set of turn-scoped refs this listener drives.
 *  2. No live send of the session may own the shared turn state. The per-send
 *     listeners write the same component-global state this listener does
 *     (`streaming`, the reasoning buffers and timers), and the message list is
 *     shared by every tab of a session — so adopting events alongside a live
 *     send would clobber the turn the user is actually watching. This gate is
 *     deliberately SESSION-scoped, not routing-key-scoped: a live send under
 *     one routing key still means the recovery listener must not touch the
 *     turn UI, even for an event tagged with a different key. Subsumes the
 *     same-key case, since a live invocation with that key owns its own events.
 *
 * (Post-reload neither gate is tripped — a fresh renderer has no live sends and
 * the persisted tab supplies the routing key — which is exactly the case the
 * recovery listener exists for.)
 */
export function shouldAdoptRecoveredEvent(params: {
  /** `session_key` carried by the event; empty/undefined for untagged legacy ones. */
  eventSessionKey: string | undefined;
  /** Base session of the view, or null before it is known. */
  sessionKey: string | null;
  /** The tab selected right now. */
  threadId: string;
  /** A handleSend() invocation of this session still has listeners subscribed. */
  hasLiveSend: boolean;
  /** This renderer already rendered a stop for the session — see localAbortSessionsRef. */
  locallyAborted: boolean;
}): boolean {
  const { eventSessionKey, sessionKey, threadId, hasLiveSend, locallyAborted } = params;
  if (!sessionKey) return false;
  if (!isEventForView(eventSessionKey, sessionKey, threadId)) return false;
  if (hasLiveSend) return false;
  if (locallyAborted) return false;
  return true;
}

/** Minimal storage surface so the helpers are unit-testable without a DOM. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** `window.sessionStorage`, or null when unavailable (blocked / not a browser). */
export function safeSessionStorage(): StorageLike | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    // Access itself can throw (sandboxed iframe / disabled storage).
    return null;
  }
}

function readItem(storage: StorageLike | null, key: string): string | null {
  if (!storage) return null;
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeItem(storage: StorageLike | null, key: string, value: string): void {
  if (!storage) return;
  try {
    storage.setItem(key, value);
  } catch {
    // Storage full / disabled — persistence is best-effort.
  }
}

/**
 * The tab list to show for `sessionKey`: the persisted one, normalised, always
 * with the main tab first.  Anything unreadable falls back to just the main
 * tab — a corrupt entry must never break the chat UI.
 */
export function loadThreadTabs(sessionKey: string, storage: StorageLike | null): ThreadTab[] {
  const tabs: ThreadTab[] = [MAIN_THREAD_TAB];
  const seen = new Set<string>([MAIN_THREAD_ID]);
  const raw = readItem(storage, threadTabsStorageKey(sessionKey));
  if (!raw) return tabs;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return tabs;
  }
  if (!Array.isArray(parsed)) return tabs;
  for (const row of parsed) {
    const threadId = typeof (row as ThreadTab)?.threadId === 'string' ? row.threadId : '';
    if (!threadId || seen.has(threadId)) continue;
    seen.add(threadId);
    tabs.push({
      threadId,
      agentType: typeof row.agentType === 'string' ? row.agentType : 'agent',
      label: typeof row.label === 'string' ? row.label : threadId,
    });
  }
  return tabs;
}

export function saveThreadTabs(
  sessionKey: string,
  tabs: readonly ThreadTab[],
  storage: StorageLike | null
): void {
  writeItem(storage, threadTabsStorageKey(sessionKey), JSON.stringify(tabs));
}

/** The tab to select: the persisted one when it still exists, else main. */
export function loadActiveThread(
  sessionKey: string,
  tabs: readonly ThreadTab[],
  storage: StorageLike | null
): string {
  const raw = readItem(storage, activeThreadStorageKey(sessionKey));
  if (!raw) return MAIN_THREAD_ID;
  return tabs.some((t) => t.threadId === raw) ? raw : MAIN_THREAD_ID;
}

export function saveActiveThread(
  sessionKey: string,
  threadId: string,
  storage: StorageLike | null
): void {
  writeItem(storage, activeThreadStorageKey(sessionKey), threadId);
}

/** Both halves of the persisted state, read in one go. */
export function loadThreadState(sessionKey: string, storage: StorageLike | null): ThreadTabsState {
  const tabs = loadThreadTabs(sessionKey, storage);
  return { tabs, active: loadActiveThread(sessionKey, tabs, storage) };
}

/** Append a spawned sub-agent's tab (no-op when it is already known). */
export function addThreadTab(state: ThreadTabsState, tab: ThreadTab): ThreadTabsState {
  if (state.tabs.some((t) => t.threadId === tab.threadId)) return state;
  return { ...state, tabs: [...state.tabs, tab] };
}

/** Select a tab; unknown ids are ignored (never select a tab that is not shown). */
export function selectThreadTab(state: ThreadTabsState, threadId: string): ThreadTabsState {
  if (state.active === threadId) return state;
  if (!state.tabs.some((t) => t.threadId === threadId)) return state;
  return { ...state, active: threadId };
}

/** Close a tab (the main tab cannot be closed); falls back to the main tab. */
export function closeThreadTab(state: ThreadTabsState, threadId: string): ThreadTabsState {
  if (threadId === MAIN_THREAD_ID) return state;
  const tabs = state.tabs.filter((t) => t.threadId !== threadId);
  if (tabs.length === state.tabs.length) return state;
  return { tabs, active: state.active === threadId ? MAIN_THREAD_ID : state.active };
}
