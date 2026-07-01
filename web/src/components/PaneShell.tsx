/**
 * PaneShell — CSS Grid-based resizable split-pane layout.
 *
 * Ported from Desktop's `components/pane-shell/pane-shell.tsx`.
 * Uses Zustand stores instead of Nano Stores.
 *
 * Usage:
 *   <PaneShell>
 *     <Pane id="sidebar" side="left" defaultOpen>...</Pane>
 *     <PaneMain>...</PaneMain>
 *     <Pane id="preview" side="right">...</Pane>
 *   </PaneShell>
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { usePanesStore, CHAT_SIDEBAR_PANE_ID, PREVIEW_PANE_ID, type PaneStateSnapshot } from "@/store/panes";
import { useLayoutStore } from "@/store/layout";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants (mirror Desktop)
// ---------------------------------------------------------------------------

const DEFAULT_WIDTH = "16rem";
const MIN_WIDTH_PX = 160;
const SASH_SIZE_X = 4; // px
const HOVER_REVEAL_DELAY_MS = 130;

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface PaneShellContextValue {
  registerPane: (desc: PaneDescriptor) => () => void;
  panes: PaneDescriptor[];
  flip: boolean;
  narrow: boolean;
}

interface PaneDescriptor {
  id: string;
  side: "left" | "right";
  resizable: boolean;
  defaultWidth: string;
  snap: PaneStateSnapshot | undefined;
}

const PaneShellContext = createContext<PaneShellContextValue | null>(null);

function usePaneShellContext() {
  const ctx = useContext(PaneShellContext);
  if (!ctx) throw new Error("PaneShell components must be inside <PaneShell>");
  return ctx;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function widthToPx(w: string, containerWidth: number): number {
  if (w.endsWith("px")) return parseFloat(w);
  if (w.endsWith("rem"))
    return parseFloat(w) * 16; // assume 16px base
  if (w.endsWith("vw")) return (parseFloat(w) / 100) * containerWidth;
  return parseFloat(w) || MIN_WIDTH_PX;
}

function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, px));
}

// ---------------------------------------------------------------------------
// PaneShell
// ---------------------------------------------------------------------------

interface PaneShellProps {
  children: ReactNode;
  className?: string;
}

export function PaneShell({ children, className }: PaneShellProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [descriptors, setDescriptors] = useState<PaneDescriptor[]>([]);
  const panesFlipped = useLayoutStore((s) => s.panesFlipped);
  const narrowViewport = useLayoutStore((s) => s.narrowViewport);
  const snapshots = usePanesStore((s) => s.paneStates);

  // Collect Pane children descriptors
  const registerPane = useCallback(
    (desc: PaneDescriptor) => {
      setDescriptors((prev) => {
        if (prev.some((d) => d.id === desc.id)) return prev;
        return [...prev, desc];
      });
      return () => {
        setDescriptors((prev) => prev.filter((d) => d.id !== desc.id));
      };
    },
    [],
  );

  // We use a different approach: Pane components push themselves via context
  const ctxValue = useMemo<PaneShellContextValue>(
    () => ({
      registerPane,
      panes: descriptors,
      flip: panesFlipped,
      narrow: narrowViewport,
    }),
    [registerPane, descriptors, panesFlipped, narrowViewport],
  );

  // Build grid template columns in a fixed left-to-right order.
  const gridStyle = useMemo<CSSProperties>(() => {
    const byId = new Map(descriptors.map((d) => [d.id, d]));
    const tracks: string[] = [];

    const leftOrder = ["side-rail", CHAT_SIDEBAR_PANE_ID];
    const rightOrder = [PREVIEW_PANE_ID];

    for (const id of leftOrder) {
      const pane = byId.get(id);
      if (!pane) continue;
      const snap = snapshots[id];
      if (!snap?.open && !narrowViewport) { tracks.push("0px"); continue; }
      tracks.push(snap?.widthOverride ? `${snap.widthOverride}px` : pane.defaultWidth);
    }

    tracks.push("1fr");

    for (const id of rightOrder) {
      const pane = byId.get(id);
      if (!pane) continue;
      const snap = snapshots[id];
      if (!snap?.open) { tracks.push("0px"); continue; }
      tracks.push(snap?.widthOverride ? `${snap.widthOverride}px` : pane.defaultWidth);
    }

    return {
      display: "grid",
      gridTemplateColumns: tracks.join(" "),
      gridTemplateRows: "1fr",
      height: "100%",
      width: "100%",
      overflow: "hidden",
    };
  }, [descriptors, snapshots, narrowViewport]);

  return (
    <PaneShellContext.Provider value={ctxValue}>
      <div
        ref={containerRef}
        className={cn("pane-shell", className)}
        style={gridStyle}
      >
        {children}
      </div>
    </PaneShellContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Pane
// ---------------------------------------------------------------------------

interface PaneProps {
  id: string;
  side: "left" | "right";
  children?: ReactNode;
  /** Whether this pane can be resized by dragging its edge. */
  resizable?: boolean;
  /** Default open state. */
  defaultOpen?: boolean;
  /** Default width CSS value. */
  defaultWidth?: string;
  /** Force collapsed (overrides store). */
  forceCollapsed?: boolean;
  /** Show pane on hover when collapsed. */
  hoverReveal?: boolean;
  className?: string;
}

export function Pane({
  id,
  side,
  children,
  resizable = true,
  defaultOpen = false,
  defaultWidth = DEFAULT_WIDTH,
  forceCollapsed,
  hoverReveal,
  className,
}: PaneProps) {
  const { registerPane, narrow } = usePaneShellContext();
  const snap = usePanesStore((s) => s.paneStates[id]);
  const setPaneWidthOverride = usePanesStore((s) => s.setPaneWidthOverride);
  const ensurePaneRegistered = usePanesStore((s) => s.ensurePaneRegistered);

  const open = forceCollapsed ? false : (snap?.open ?? defaultOpen);
  const isOpen = open || (narrow && side === "left");

  // Hover reveal state
  const [hoverRevealed, setHoverRevealed] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Register on mount
  useEffect(() => {
    ensurePaneRegistered(id, { open: defaultOpen });
    const unreg = registerPane({ id, side, resizable, defaultWidth, snap });
    return unreg;
  }, [id, side, resizable, defaultWidth, defaultOpen, ensurePaneRegistered, registerPane, snap]);

  // Drag resize
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const onSashMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!resizable) return;
      e.preventDefault();
      const currentWidth =
        snap?.widthOverride ??
        widthToPx(defaultWidth, window.innerWidth);
      dragStartRef.current = { startX: e.clientX, startWidth: currentWidth };
      setDragging(true);
    },
    [resizable, snap?.widthOverride, defaultWidth],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!dragStartRef.current) return;
      const delta = e.clientX - dragStartRef.current.startX;
      // For right-side panes, dragging left increases width
      const sign = side === "right" ? -1 : 1;
      const newWidth = clampWidth(
        dragStartRef.current.startWidth + delta * sign,
        MIN_WIDTH_PX,
        window.innerWidth * 0.5,
      );
      setPaneWidthOverride(id, newWidth);
    };
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, id, side, setPaneWidthOverride]);

  // Hover reveal handlers
  const onHoverEnter = useCallback(() => {
    if (!hoverReveal || isOpen) return;
    hoverTimerRef.current = setTimeout(
      () => setHoverRevealed(true),
      HOVER_REVEAL_DELAY_MS,
    );
  }, [hoverReveal, isOpen]);

  const onHoverLeave = useCallback(() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoverRevealed(false);
  }, []);

  const effectiveOpen = isOpen || hoverRevealed;

  // Sash edge — the drag handle (opposite side of the pane)
  const sashEdge = side === "left" ? "right" : "left";

  return (
    <div
      data-pane-id={id}
      data-pane-side={side}
      data-pane-open={effectiveOpen}
      className={cn(
        "relative flex h-full min-h-0 flex-col overflow-hidden",
        "transition-[width] duration-200 ease-out",
        !effectiveOpen && "w-0! overflow-hidden!",
        className,
      )}
      style={{
        ...(effectiveOpen ? {} : { visibility: "hidden" }),
      }}
      onMouseEnter={onHoverEnter}
      onMouseLeave={onHoverLeave}
    >
      {effectiveOpen && children}

      {/* Drag sash */}
      {resizable && effectiveOpen && (
        <div
          data-pane-sash={sashEdge}
          className={cn(
            "absolute top-0 z-20 h-full",
            "cursor-col-resize select-none",
            sashEdge === "left"
              ? "left-0"
              : "right-0",
          )}
          style={{
            width: SASH_SIZE_X,
            [sashEdge]: -SASH_SIZE_X / 2,
          }}
          onMouseDown={onSashMouseDown}
        />
      )}

      {/* Hover reveal overlay (collapsed state) */}
      {hoverReveal && !isOpen && hoverRevealed && (
        <div
          data-pane-overlay
          className="absolute inset-0 z-30 bg-(--dt-panel-strong) shadow-(--dt-shadow-lg)"
          style={{ width: defaultWidth }}
        >
          {children}
        </div>
      )}

      {/* Drag overlay */}
      {dragging && (
        <div
          className="fixed inset-0 z-50 cursor-col-resize"
          style={{ userSelect: "none" }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PaneMain
// ---------------------------------------------------------------------------

interface PaneMainProps {
  children?: ReactNode;
  className?: string;
}

export function PaneMain({ children, className }: PaneMainProps) {
  return (
    <div
      data-pane-main
      className={cn(
        "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
        className,
      )}
    >
      {children}
    </div>
  );
}
