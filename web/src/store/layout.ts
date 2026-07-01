/**
 * Layout store — mirrors Desktop's `src/store/layout.ts` with Zustand.
 *
 * Manages sidebar visibility, width, pane flipping, and viewport state.
 * API is intentionally aligned with Nano Stores patterns so a future
 * migration to Nano Stores is a mechanical rename, not a rewrite.
 */

import { create } from "zustand";

// ---------------------------------------------------------------------------
// Constants (mirror Desktop's layout-constants + layout store)
// ---------------------------------------------------------------------------

export const SIDEBAR_DEFAULT_WIDTH = 237;
export const SIDEBAR_MAX_WIDTH = 360;
export const SIDEBAR_COLLAPSE_BREAKPOINT_PX = 768;

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface LayoutState {
  /** Whether the left sidebar is open. */
  sidebarOpen: boolean;
  /** Sidebar width in pixels (clamped between default and max). */
  sidebarWidth: number;
  /** Whether the sidebar is currently being resized by the user. */
  isSidebarResizing: boolean;
  /** Whether panes are flipped (right rail on left side). */
  panesFlipped: boolean;
  /** Whether the viewport is narrow (< collapse breakpoint). */
  narrowViewport: boolean;

  // Actions
  setSidebarOpen: (open: boolean) => void;
  toggleSidebarOpen: () => void;
  setSidebarWidth: (width: number) => void;
  setSidebarResizing: (resizing: boolean) => void;
  togglePanesFlipped: () => void;
  setNarrowViewport: (narrow: boolean) => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  // State
  sidebarOpen: true,
  sidebarWidth: SIDEBAR_DEFAULT_WIDTH,
  isSidebarResizing: false,
  panesFlipped: false,
  narrowViewport: false,

  // Actions
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  toggleSidebarOpen: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarWidth: (width) =>
    set({
      sidebarWidth: Math.min(
        SIDEBAR_MAX_WIDTH,
        Math.max(SIDEBAR_DEFAULT_WIDTH, width),
      ),
    }),
  setSidebarResizing: (resizing) => set({ isSidebarResizing: resizing }),
  togglePanesFlipped: () => set((s) => ({ panesFlipped: !s.panesFlipped })),
  setNarrowViewport: (narrow) => set({ narrowViewport: narrow }),
}));

// ---------------------------------------------------------------------------
// Media query hook — call once at app root to sync narrowViewport
// ---------------------------------------------------------------------------

export function syncNarrowViewport(): () => void {
  const mq = window.matchMedia(
    `(max-width: ${SIDEBAR_COLLAPSE_BREAKPOINT_PX}px)`,
  );
  const update = () => useLayoutStore.getState().setNarrowViewport(mq.matches);
  update();
  mq.addEventListener("change", update);
  return () => mq.removeEventListener("change", update);
}
