import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface RestartRequiredContextValue {
  restartRequired: boolean;
  /** Human-readable reasons why a restart is required (tier C config, #789). */
  restartReasons: string[];
  markRestartRequired: (reasons?: string[]) => void;
  clearRestartRequired: () => void;
}

const RestartRequiredContext = createContext<RestartRequiredContextValue>({
  restartRequired: false,
  restartReasons: [],
  markRestartRequired: () => {},
  clearRestartRequired: () => {},
});

export function RestartRequiredProvider({ children }: { children: ReactNode }) {
  const [restartRequired, setRestartRequired] = useState(false);
  const [restartReasons, setRestartReasons] = useState<string[]>([]);

  const markRestartRequired = useCallback((reasons?: string[]) => {
    setRestartRequired(true);
    if (reasons && reasons.length > 0) {
      setRestartReasons((prev) => {
        const merged = [...prev, ...reasons];
        return [...new Set(merged)];
      });
    }
  }, []);

  const clearRestartRequired = useCallback(() => {
    setRestartRequired(false);
    setRestartReasons([]);
  }, []);

  return (
    <RestartRequiredContext.Provider
      value={{ restartRequired, restartReasons, markRestartRequired, clearRestartRequired }}
    >
      {children}
    </RestartRequiredContext.Provider>
  );
}

export function useRestartRequired() {
  return useContext(RestartRequiredContext);
}
