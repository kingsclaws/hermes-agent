/**
 * Panes store — mirrors Desktop's `src/store/panes.ts` with Zustand.
 *
 * Manages the state of individual resizable panes: open/closed,
 * width/height overrides. Persisted to localStorage.
 */

import { create } from "zustand";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PaneStateSnapshot {
  open: boolean;
  widthOverride?: number;
  heightOverride?: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STORAGE_KEY = "hermes.web.paneStates.v1";
export const CHAT_SIDEBAR_PANE_ID = "chat-sidebar";
export const FILE_BROWSER_PANE_ID = "file-browser";
export const PREVIEW_PANE_ID = "preview";

// ---------------------------------------------------------------------------
// Persistence helpers
// ---------------------------------------------------------------------------

function load(): Record<string, PaneStateSnapshot> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, PaneStateSnapshot> = {};
    for (const [key, val] of Object.entries(
      parsed as Record<string, unknown>,
    )) {
      if (!val || typeof val !== "object") continue;
      const v = val as Record<string, unknown>;
      if (typeof v.open !== "boolean") continue;
      const snapshot: PaneStateSnapshot = { open: v.open };
      if (typeof v.widthOverride === "number" && isFinite(v.widthOverride))
        snapshot.widthOverride = v.widthOverride;
      if (typeof v.heightOverride === "number" && isFinite(v.heightOverride))
        snapshot.heightOverride = v.heightOverride;
      out[key] = snapshot;
    }
    return out;
  } catch {
    return {};
  }
}

function persist(states: Record<string, PaneStateSnapshot>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(states));
  } catch {
    // localStorage full — silently drop.
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface PanesState {
  paneStates: Record<string, PaneStateSnapshot>;

  // Actions
  ensurePaneRegistered: (
    id: string,
    defaults: { open: boolean; widthOverride?: number },
  ) => void;
  setPaneOpen: (id: string, open: boolean) => void;
  togglePane: (id: string) => void;
  setPaneWidthOverride: (id: string, width: number | undefined) => void;
  getPaneStateSnapshot: (id: string) => PaneStateSnapshot | undefined;
}

export const usePanesStore = create<PanesState>((set, get) => {
  // Seed with known panes (mirrors Desktop's module-level ensurePaneRegistered)
  const initial = load();
  for (const [id, defaults] of [
    [CHAT_SIDEBAR_PANE_ID, { open: true }],
    [FILE_BROWSER_PANE_ID, { open: false }],
    [PREVIEW_PANE_ID, { open: true }],
  ] as const) {
    if (!initial[id]) {
      initial[id] = { open: defaults.open };
    }
  }

  return {
    paneStates: initial,

    ensurePaneRegistered: (id, defaults) => {
      const states = get().paneStates;
      if (states[id]) return;
      set({
        paneStates: {
          ...states,
          [id]: { open: defaults.open, widthOverride: defaults.widthOverride },
        },
      });
    },

    setPaneOpen: (id, open) => {
      const states = get().paneStates;
      const current = states[id];
      if (current?.open === open) return;
      const next = { ...states, [id]: { ...(current ?? { open: false }), open } };
      set({ paneStates: next });
      persist(next);
    },

    togglePane: (id) => {
      const states = get().paneStates;
      const current = states[id];
      const open = !(current?.open ?? false);
      const next = { ...states, [id]: { ...(current ?? { open: false }), open } };
      set({ paneStates: next });
      persist(next);
    },

    setPaneWidthOverride: (id, width) => {
      const states = get().paneStates;
      const current = states[id];
      if (current?.widthOverride === width) return;
      const next = {
        ...states,
        [id]: { ...(current ?? { open: false }), widthOverride: width },
      };
      set({ paneStates: next });
      persist(next);
    },

    getPaneStateSnapshot: (id) => get().paneStates[id],
  };
});
