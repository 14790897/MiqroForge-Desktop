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
import type { TimelineEntry } from '../features/chat/components/Timeline';

export type UserInputCardState = 'pending' | 'confirmed' | 'cancelled' | 'modify';

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
  createdAt?: number;
  choiceLabel?: string;
  choiceId?: string;
  resolvedAt?: number;
  timedOut?: boolean;
  backendReleased?: boolean;
  stepsStatus?: Record<string, StepExecStatus>;
}

interface UserInputContextValue {
  pending: Record<string, UserInputCardEntry>;
  resolved: Record<string, UserInputCardEntry>;
  timelines: Record<string, TimelineEntry>;
  /** Send the user's choice back to the backend (blocking tool resolves). */
  resolve: (
    inputId: string,
    choiceId: string,
    choiceLabel: string,
    remember?: boolean,
    rememberMode?: 'session' | 'always'
  ) => Promise<void>;
  /** Local timeout: flip the card to a timed-out resolved state. */
  timeoutCard: (inputId: string) => void;
  lastAdjustAt?: number;
  activeSession?: string;
  setActiveSession: (key: string) => void;
}

const UserInputContext = createContext<UserInputContextValue>({
  pending: {},
  resolved: {},
  timelines: {},
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

  const setActiveSession = useCallback((key: string) => {
    setActiveSessionState(key);
    pendingRef.current = {};
    setPending({});
    setResolved({});
    setTimelines({});
  }, []);

  const [timelines, setTimelines] = useState<Record<string, TimelineEntry>>({});

  const upsertPending = useCallback((entry: UserInputCardEntry) => {
    pendingRef.current = {
      ...pendingRef.current,
      [entry.request.input_id]: { ...entry, createdAt: entry.createdAt ?? Date.now() },
    };
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
      const data: UserInputCardRequest = {
        ...raw,
        timeout_seconds: raw.timeout_seconds ?? raw.timeoutSeconds,
        allow_remember_choice: raw.allow_remember_choice ?? raw.allowRememberChoice ?? false,
      };
      if (activeSession && data.session_key && data.session_key !== activeSession) return;

      if (data.display === 'todo_state') {
        const turnId = String(data.turn_id ?? 'todo');
        const revision = Number(raw.revision ?? 0);
        setTimelines((prev) => {
          const current = prev[turnId];
          if (current?.todoRevision !== undefined && revision <= current.todoRevision) {
            return prev;
          }
          return {
            ...prev,
            [turnId]: {
              title: (raw.title as string) ?? 'AI 正在执行任务',
              goal: (raw.goal as string) ?? '',
              steps: [],
              permissions: [],
              todoItems: Array.isArray(raw.items)
                ? raw.items.map((it: any) => ({
                    id: String(it?.id ?? ''),
                    title: String(it?.title ?? it?.content ?? ''),
                    status: String(it?.status ?? 'queued'),
                  }))
                : [],
              todoRevision: revision,
            },
          };
        });
        return;
      }

      if (raw.display === 'timeline') {
        const turnId = String(raw.turn_id ?? 'timeline');
        setTimelines((prev) => {
          const current = prev[turnId];
          const revision = Number(raw.revision ?? current?.todoRevision ?? 0);
          if (current?.todoRevision !== undefined && revision < current.todoRevision) return prev;
          return {
            ...prev,
            [turnId]: {
              title: String(raw.title ?? 'AI 正在执行任务'),
              goal: String(raw.goal ?? ''),
              steps: Array.isArray(raw.steps)
                ? raw.steps.map((s: any) => ({
                    name: String(s?.name ?? s?.title ?? ''),
                    tools: Array.isArray(s?.tools) ? s.tools : [],
                  }))
                : [],
              permissions: Array.isArray(raw.permissions) ? raw.permissions : [],
              phase: 'running',
              todoRevision: revision,
            },
          };
        });
        return;
      }

      upsertPending({ request: data, state: 'pending' });
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
    async (
      inputId: string,
      choiceId: string,
      choiceLabel: string,
      remember = false,
      rememberMode = 'session'
    ) => {
      const miqi = (window as any).miqi;
      const entry = pendingRef.current[inputId];
      const role = entry?.request.choices?.find((c) => c.id === choiceId)?.role;
      const isCancel = role === 'cancel' || (role === undefined && choiceId === 'cancel');
      const isModify = role === 'adjust' || choiceId === 'modify' || choiceId === 'adjust';
      // Optimistic update: the card flips to confirmed/cancelled/modify immediately;
      // backend user_input_resolved will reconcile (idempotent).
      moveToResolved(
        inputId,
        isCancel ? 'cancelled' : isModify ? 'modify' : 'confirmed',
        choiceId,
        choiceLabel,
        false,
        role
      );
      try {
        const res = await miqi?.userInput?.resolve(
          inputId,
          choiceId,
          choiceLabel,
          remember,
          rememberMode
        );
        if (res && res.resolved === false && entry) {
          markBackendReleased(inputId);
        }
      } catch {
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
        timelines,
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
