import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface StatusBarState {
  connectionStatus: "connected" | "disconnected" | "connecting";
  sessionId: string | null;
  projectName: string | null;
  gitBranch: string | null;
  tokenUsage: { used: number; total: number } | null;
  lastActivity: string | null;
}

export interface StatusBarContextValue extends StatusBarState {
  setConnectionStatus: (status: StatusBarState["connectionStatus"]) => void;
  setSession: (id: string | null) => void;
  setProject: (name: string | null, branch?: string | null) => void;
  setTokenUsage: (usage: { used: number; total: number } | null) => void;
  setLastActivity: (label: string | null) => void;
  update: (patch: Partial<StatusBarState>) => void;
}

const StatusBarContext = createContext<StatusBarContextValue | null>(null);

export function StatusBarProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StatusBarState>({
    connectionStatus: "connected",
    sessionId: null,
    projectName: null,
    gitBranch: null,
    tokenUsage: null,
    lastActivity: null,
  });

  const update = useCallback(
    (patch: Partial<StatusBarState>) => setState((prev) => ({ ...prev, ...patch })),
    [],
  );

  const setConnectionStatus = useCallback(
    (status: StatusBarState["connectionStatus"]) => setState((prev) => ({ ...prev, connectionStatus: status })),
    [],
  );

  const setSession = useCallback(
    (id: string | null) => setState((prev) => ({ ...prev, sessionId: id })),
    [],
  );

  const setProject = useCallback(
    (name: string | null, branch?: string | null) =>
      setState((prev) => ({ ...prev, projectName: name, gitBranch: branch ?? prev.gitBranch })),
    [],
  );

  const setTokenUsage = useCallback(
    (usage: { used: number; total: number } | null) => setState((prev) => ({ ...prev, tokenUsage: usage })),
    [],
  );

  const setLastActivity = useCallback(
    (label: string | null) => setState((prev) => ({ ...prev, lastActivity: label })),
    [],
  );

  const value = useMemo<StatusBarContextValue>(
    () => ({ ...state, setConnectionStatus, setSession, setProject, setTokenUsage, setLastActivity, update }),
    [state, setConnectionStatus, setSession, setProject, setTokenUsage, setLastActivity, update],
  );

  return <StatusBarContext.Provider value={value}>{children}</StatusBarContext.Provider>;
}

export function useStatusBar(): StatusBarContextValue {
  const ctx = useContext(StatusBarContext);
  if (!ctx) throw new Error("useStatusBar must be used within StatusBarProvider");
  return ctx;
}
