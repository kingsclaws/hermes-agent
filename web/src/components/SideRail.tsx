/**
 * SideRail — Desktop-style icon column on the far left.
 *
 * Narrow vertical bar (~3rem) with stacked icons for primary navigation.
 * Each icon has a tooltip that appears on hover. Active item has an
 * accent-colour indicator bar on its left edge.
 *
 * Mirrors Desktop's left rail (Chat, Skills, Starmap, Profiles, Settings).
 */

import { useState, useRef, type ComponentType } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SideRailItem {
  id: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  /** Optional badge count (e.g. unread messages). */
  badge?: number;
}

interface SideRailProps {
  items: SideRailItem[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Slot at the top (e.g. brand mark). */
  topSlot?: React.ReactNode;
  /** Slot at the bottom (e.g. settings gear, user avatar). */
  bottomSlot?: React.ReactNode;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SideRail({
  items,
  activeId,
  onSelect,
  topSlot,
  bottomSlot,
}: SideRailProps) {
  return (
    <nav
      data-side-rail
      className="flex h-full w-12 shrink-0 flex-col items-center border-r"
      style={{
        background: "var(--dt-sidebar)",
        borderColor: "var(--sidebar-edge-border)",
      }}
      aria-label="Primary navigation"
    >
      {/* Top slot — brand icon, etc. */}
      {topSlot && (
        <div className="flex h-12 w-full shrink-0 items-center justify-center">
          {topSlot}
        </div>
      )}

      {/* Nav items */}
      <div className="flex min-h-0 flex-1 flex-col items-center gap-0.5 py-1">
        {items.map((item) => (
          <SideRailButton
            key={item.id}
            item={item}
            active={item.id === activeId}
            onClick={() => onSelect(item.id)}
          />
        ))}
      </div>

      {/* Bottom slot — settings, user */}
      {bottomSlot && (
        <div className="mb-1 flex w-full flex-col items-center gap-0.5">
          {bottomSlot}
        </div>
      )}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// SideRailButton
// ---------------------------------------------------------------------------

function SideRailButton({
  item,
  active,
  onClick,
}: {
  item: SideRailItem;
  active: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [hovered, setHovered] = useState(false);
  const Icon = item.icon;

  return (
    <div className="relative flex w-full justify-center">
      {/* Active indicator — left-edge accent bar */}
      <div
        className={cn(
          "absolute left-0 top-1/2 -translate-y-1/2 transition-all duration-150",
          active ? "h-5 w-0.5 rounded-r-full" : "h-0 w-0.5 rounded-r-full",
        )}
        style={{ background: active ? "var(--theme-primary)" : "transparent" }}
      />

      <button
        ref={ref}
        type="button"
        onClick={onClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        aria-label={item.label}
        aria-current={active ? "page" : undefined}
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-md transition-all duration-100",
        )}
        style={{
          color: active ? "var(--text-high)" : "var(--text-low)",
          background: active
            ? "var(--accent-fill-2)"
            : hovered
              ? "var(--accent-fill-1)"
              : "transparent",
        }}
      >
        <Icon className="h-4.5 w-4.5" />

        {/* Badge */}
        {item.badge != null && item.badge > 0 && (
          <span className="absolute right-0.5 top-0.5 flex h-3.5 min-w-[0.875rem] items-center justify-center rounded-full px-0.5 text-[0.55rem] font-bold leading-none text-white"
            style={{ background: "var(--dt-destructive)" }}
          >
            {item.badge > 99 ? "99+" : item.badge}
          </span>
        )}
      </button>

      {/* Tooltip */}
      {hovered && ref.current && (
        <SideRailTooltip anchor={ref.current} label={item.label} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

function SideRailTooltip({
  anchor,
  label,
}: {
  anchor: HTMLElement;
  label: string;
}) {
  const rect = anchor.getBoundingClientRect();
  return (
    <div
      className="pointer-events-none fixed z-[200] rounded px-2.5 py-1 text-[0.7rem] font-medium shadow-lg"
      style={{
        left: rect.right + 8,
        top: rect.top + rect.height / 2,
        transform: "translateY(-50%)",
        background: "var(--dt-panel-strong)",
        color: "var(--text-high)",
        border: "1px solid var(--ui-stroke-secondary)",
        whiteSpace: "nowrap",
      }}
    >
      {label}
      {/* Arrow */}
      <div
        className="absolute top-1/2 -translate-y-1/2"
        style={{
          left: -4,
          width: 0,
          height: 0,
          borderTop: "4px solid transparent",
          borderBottom: "4px solid transparent",
          borderRight: "4px solid var(--ui-stroke-secondary)",
        }}
      />
    </div>
  );
}
