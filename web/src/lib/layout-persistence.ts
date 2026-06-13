const KEY_PREFIX = "hermes-panel:";
const WORKSPACE_KEY = "hermes-workspace";

const DEFAULTS: Record<string, number> = {
  sidebar: 15,
  "chat-main": 65,
  "chat-sidebar": 35,
  "files-list": 60,
  "files-detail": 40,
  "native-messages": 60,
  "native-inspector": 40,
};

export function loadPanelSize(id: string): number {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + id);
    if (raw !== null) {
      const n = parseFloat(raw);
      if (!Number.isNaN(n) && n > 0) return n;
    }
  } catch {
    // localStorage unavailable
  }
  return DEFAULTS[id] ?? 50;
}

export function savePanelSize(id: string, size: number): void {
  try {
    localStorage.setItem(KEY_PREFIX + id, String(Math.round(size * 10) / 10));
  } catch {
    // silently ignore
  }
  // Also update workspace state
  updateWorkspace();
}

export function loadPanelSizes(ids: string[]): Record<string, number> {
  const result: Record<string, number> = {};
  for (const id of ids) {
    result[id] = loadPanelSize(id);
  }
  return result;
}

// ---- Workspace-level persistence ----

export interface WorkspaceState {
  panelSizes: Record<string, number>;
  panelVisibility: Record<string, boolean>;
  sidebarCollapsed: boolean;
}

const DEFAULT_WORKSPACE: WorkspaceState = {
  panelSizes: {},
  panelVisibility: {},
  sidebarCollapsed: false,
};

let _workspaceTimeout: ReturnType<typeof setTimeout> | null = null;

function updateWorkspace(): void {
  if (_workspaceTimeout) clearTimeout(_workspaceTimeout);
  _workspaceTimeout = setTimeout(() => {
    // Workspace is saved lazily via saveWorkspaceState
    _workspaceTimeout = null;
  }, 500);
}

export function loadWorkspaceState(): WorkspaceState {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        panelSizes: {},
        panelVisibility: {},
        sidebarCollapsed: parsed.sidebarCollapsed ?? false,
      };
    }
  } catch {
    // ignore
  }
  return { ...DEFAULT_WORKSPACE };
}

export function saveWorkspaceState(state: Partial<WorkspaceState>): void {
  try {
    const existing = loadWorkspaceState();
    const merged: WorkspaceState = {
      panelSizes: { ...existing.panelSizes, ...state.panelSizes },
      panelVisibility: {
        ...existing.panelVisibility,
        ...state.panelVisibility,
      },
      sidebarCollapsed:
        state.sidebarCollapsed ?? existing.sidebarCollapsed,
    };
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify(merged));
  } catch {
    // ignore
  }
}

export function resetWorkspace(): void {
  try {
    localStorage.removeItem(WORKSPACE_KEY);
    // Also clear individual panel size keys
    for (const key of Object.keys(DEFAULTS)) {
      localStorage.removeItem(KEY_PREFIX + key);
    }
  } catch {
    // ignore
  }
}
