import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  loadWorkspaceState,
  saveWorkspaceState,
  resetWorkspace,
} from "@/lib/layout-persistence";

export interface DockManagerContextValue {
  panelVisible: (panelId: string) => boolean;
  togglePanel: (panelId: string) => void;
  setPanelVisible: (panelId: string, visible: boolean) => void;
  resetLayout: () => void;
}

const DockManagerContext = createContext<DockManagerContextValue | null>(null);

export function DockManagerProvider({ children }: { children: ReactNode }) {
  const [visibility, setVisibility] = useState<Record<string, boolean>>(() => {
    const ws = loadWorkspaceState();
    return ws.panelVisibility ?? {};
  });

  const panelVisible = useCallback(
    (panelId: string) => visibility[panelId] !== false,
    [visibility],
  );

  const setPanelVisible = useCallback(
    (panelId: string, visible: boolean) => {
      setVisibility((prev) => {
        const next = { ...prev, [panelId]: visible };
        saveWorkspaceState({ panelVisibility: next });
        return next;
      });
    },
    [],
  );

  const togglePanel = useCallback(
    (panelId: string) => {
      setPanelVisible(panelId, !panelVisible(panelId));
    },
    [panelVisible, setPanelVisible],
  );

  const resetLayout = useCallback(() => {
    resetWorkspace();
    setVisibility({});
  }, []);

  const value = useMemo<DockManagerContextValue>(
    () => ({ panelVisible, togglePanel, setPanelVisible, resetLayout }),
    [panelVisible, togglePanel, setPanelVisible, resetLayout],
  );

  return (
    <DockManagerContext.Provider value={value}>
      {children}
    </DockManagerContext.Provider>
  );
}

export function useDockManager(): DockManagerContextValue {
  const ctx = useContext(DockManagerContext);
  if (!ctx)
    throw new Error("useDockManager must be used within DockManagerProvider");
  return ctx;
}
