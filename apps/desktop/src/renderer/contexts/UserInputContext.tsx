import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import type { UserInputCardRequest, UserInputResolvedData } from '../../shared/ipc';

export type UserInputCardState = 'pending' | 'confirmed' | 'cancelled';

/** 步骤执行状态（v5 live 态）：展示层 best-effort，数据由 Step 事件填充。 */
export interface StepExecStatus {
  status: 'pending' | 'running' | 'success' | 'failed';
  result?: string;
  dur?: string;
  tool?: string;
  param?: string;
}

export interface UserInputCardEntry {
  request: UserInputCardRequest;
  state: UserInputCardState;
  /** User's final choice, filled once resolved (issue #646). */
  choiceLabel?: string;
  choiceId?: string;
  resolvedAt?: number;
  /** Local timeout (legacy path has no resolved event on timeout). */
  timedOut?: boolean;
  /** The backend had already released the request (timeout / turn end /
   *  concurrent-card rejection) when the user clicked — the card is closed
   *  as processed instead of being restored to pending (issue #714). */
  backendReleased?: boolean;
  /** Step live states keyed by step id (v5 execution mode). */
  stepsStatus?: Record<string, StepExecStatus>;
}

interface UserInputContextValue {
  /** Active (pending) cards keyed by input_id — at most one per turn. */
  pending: Record<string, UserInputCardEntry>;
  /** Resolved cards kept in the message flow for traceability. */
  resolved: Record<string, UserInputCardEntry>;
  /** Send the user's choice back to the backend (blocking tool resolves). */
  resolve: (
    inputId: string,
    choiceId: string,
    choiceLabel: string,
    remember?: boolean
  ) => Promise<void>;
  /** Local timeout: flip the card to a timed-out resolved state. */
  timeoutCard: (inputId: string) => void;
  /** Timestamp of the last "adjust" resolution — composer focuses for input. */
  lastAdjustAt?: number;
  /** Active session — cards from other sessions are ignored; switching
   *  sessions drops all cards (CodeRabbit #666 review). */
  activeSession?: string;
  setActiveSession: (key: string) => void;
}

const UserInputContext = createContext<UserInputContextValue>({
  pending: {},
  resolved: {},
  resolve: async () => {},
  timeoutCard: () => {},
  lastAdjustAt: undefined,
  activeSession: undefined,
  setActiveSession: () => {},
});

export function UserInputProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Record<string, UserInputCardEntry>>({});
  const [resolved, setResolved] = useState<Record<string, UserInputCardEntry>>({});
  const [lastAdjustAt, setLastAdjustAt] = useState<number | undefined>(undefined);
  const [activeSession, setActiveSessionState] = useState<string | undefined>(undefined);
  const pendingRef = useRef<Record<string, UserInputCardEntry>>({});

  // 切会话：清空全部卡片（pending + resolved），避免跨会话混卡
  const setActiveSession = useCallback((key: string) => {
    setActiveSessionState(key);
    pendingRef.current = {};
    setPending({});
    setResolved({});
  }, []);

  const upsertPending = useCallback((entry: UserInputCardEntry) => {
    pendingRef.current = { ...pendingRef.current, [entry.request.input_id]: entry };
    setPending(pendingRef.current);
  }, []);

  const moveToResolved = useCallback(
    (
      inputId: string,
      state: UserInputCardState,
      choiceId?: string,
      choiceLabel?: string,
      timedOut = false,
      role?: string
    ) => {
      const entry = pendingRef.current[inputId];
      if (!entry) return;
      const done: UserInputCardEntry = {
        ...entry,
        state,
        choiceId,
        choiceLabel,
        resolvedAt: Date.now(),
        timedOut,
      };
      pendingRef.current = { ...pendingRef.current };
      delete pendingRef.current[inputId];
      setPending(pendingRef.current);
      setResolved((prev) => ({ ...prev, [inputId]: done }));
      // "adjust" → composer should focus for the user's adjustment text.
      // Prefer the semantic role; fall back to the literal id (issue #646).
      const isAdjust = role === 'adjust' || (role === undefined && choiceId === 'adjust');
      if (isAdjust) setLastAdjustAt(Date.now());
    },
    []
  );

  const timeoutCard = useCallback(
    (inputId: string) => {
      moveToResolved(inputId, 'cancelled', undefined, undefined, true);
    },
    [moveToResolved]
  );

  // Backend no longer holds the request (timed out / turn ended / rejected
  // as a concurrent card): the optimistic flip already closed the card, so
  // record it as backend-released — restoring it to pending would create a
  // zombie card that bounces closed→pending on every click (issue #714).
  const markBackendReleased = useCallback((inputId: string) => {
    setResolved((prev) => {
      const existing = prev[inputId];
      if (!existing) return prev;
      return {
        ...prev,
        [inputId]: {
          ...existing,
          state: 'cancelled',
          choiceId: undefined,
          choiceLabel: undefined,
          backendReleased: true,
          resolvedAt: existing.resolvedAt ?? Date.now(),
        },
      };
    });
  }, []);

  useEffect(() => {
    const miqi = (window as any).miqi;
    if (!miqi?.userInput) return;
    const unsubReq = miqi.userInput.onRequest((raw: any) => {
      // 归一化两种载荷来源（CodeRabbit #711）：legacy 桥（snake_case）
      // 与 KUN 事件（camelCase timeoutSeconds/allowRememberChoice）。
      const data: UserInputCardRequest = {
        ...raw,
        timeout_seconds: raw.timeout_seconds ?? raw.timeoutSeconds,
        allow_remember_choice: raw.allow_remember_choice ?? raw.allowRememberChoice ?? false,
      };
      // 会话隔离：非当前会话的卡不渲染（data.session_key 缺省时放行）
      if (activeSession && data.session_key && data.session_key !== activeSession) return;
      upsertPending({
        request: data,
        state: 'pending',
      });
    });
    const unsubRes = miqi.userInput.onResolved((data: UserInputResolvedData) => {
      if (data.status === 'cancelled') {
        moveToResolved(data.input_id, 'cancelled');
      } else {
        const res = data.resolution ?? {};
        moveToResolved(
          data.input_id,
          'confirmed',
          typeof res.choice_id === 'string' ? res.choice_id : undefined,
          typeof res.choice_label === 'string' ? res.choice_label : undefined
        );
      }
    });
    return () => {
      unsubReq();
      unsubRes();
    };
  }, [upsertPending, moveToResolved, activeSession]);

  const resolve = useCallback(
    async (inputId: string, choiceId: string, choiceLabel: string, remember = false) => {
      const miqi = (window as any).miqi;
      // Classify by semantic role (falling back to the literal id) instead of
      // hard-coding 'cancel' — a caller-supplied cancel id like 'abort'/'no'
      // must also resolve as cancelled (issue #646 review).
      const entry = pendingRef.current[inputId];
      const role = entry?.request.choices?.find((c) => c.id === choiceId)?.role;
      const isCancel = role === 'cancel' || (role === undefined && choiceId === 'cancel');
      // Optimistic update: the card flips to confirmed/cancelled immediately;
      // backend user_input_resolved will reconcile (idempotent).
      moveToResolved(
        inputId,
        isCancel ? 'cancelled' : 'confirmed',
        choiceId,
        choiceLabel,
        false,
        role
      );
      try {
        const res = await miqi?.userInput?.resolve(inputId, choiceId, choiceLabel, remember);
        if (res && res.resolved === false && entry) {
          // Backend no longer holds the request (timeout / turn end /
          // concurrent-card rejection) — no user_input_resolved event will
          // arrive. The optimistic flip already closed the card: keep it
          // closed as backend-released instead of restoring it to pending,
          // which would loop close→pending on every click (issue #714).
          markBackendReleased(inputId);
        }
      } catch {
        // IPC failure — restore the card so the user can retry; the backend
        // still holds the request and resolves via timeout/turn-stop as a
        // last resort. Drop the optimistic resolved copy first, otherwise
        // the card renders twice: as the interactive pending card AND as a
        // resolved history row (CodeRabbit #716).
        if (entry) {
          setResolved((prev) => {
            if (!(inputId in prev)) return prev;
            const next = { ...prev };
            delete next[inputId];
            return next;
          });
          upsertPending({ ...entry, state: 'pending' });
        }
      }
    },
    [moveToResolved, upsertPending, markBackendReleased]
  );

  return (
    <UserInputContext.Provider
      value={{
        pending,
        resolved,
        resolve,
        timeoutCard,
        lastAdjustAt,
        activeSession,
        setActiveSession,
      }}
    >
      {children}
    </UserInputContext.Provider>
  );
}

export function useUserInput() {
  return useContext(UserInputContext);
}
