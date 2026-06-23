import { useState, useCallback } from "react";
import { loadWorkspaceState, saveWorkspaceState } from "@/lib/layout-persistence";

export type RightPanelTab = "info" | "kanban" | "tools";

const VIS_KEY = "right-panel";
const TAB_STORAGE_KEY = "hermes-panel:rightPanelTab";

export function useRightPanel() {
  const [expanded, setExpanded] = useState(() => {
    const ws = loadWorkspaceState();
    return ws.panelVisibility?.[VIS_KEY] ?? false;
  });

  const [activeTab, setActiveTab] = useState<RightPanelTab>(() => {
    try {
      return (localStorage.getItem(TAB_STORAGE_KEY) as RightPanelTab) ?? "info";
    } catch {
      return "info";
    }
  });

  const toggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      saveWorkspaceState({ panelVisibility: { [VIS_KEY]: next } });
      return next;
    });
  }, []);

  const expand = useCallback(() => {
    setExpanded(true);
    saveWorkspaceState({ panelVisibility: { [VIS_KEY]: true } });
  }, []);

  const changeTab = useCallback((tab: RightPanelTab) => {
    setActiveTab(tab);
    try {
      localStorage.setItem(TAB_STORAGE_KEY, tab);
    } catch {
      // ignore
    }
  }, []);

  return { expanded, activeTab, toggle, expand, changeTab };
}
