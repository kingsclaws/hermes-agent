import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentType,
} from "react";
import { createPortal } from "react-dom";
import { NavLink, useNavigate } from "react-router-dom";
import {
  Download,
  PanelLeft,
  RotateCw,
} from "lucide-react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";
import type { Translations } from "@/i18n/types";
import { gatewayLine } from "@/components/SidebarStatusStrip";
import type { StatusResponse } from "@/lib/api";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useSystemActions } from "@/contexts/useSystemActions";
import type { SystemAction } from "@/contexts/system-actions-context";
import { PluginSlot } from "@/plugins";

// ---- Public types ----

export interface NavItem {
  icon: ComponentType<{ className?: string }>;
  label: string;
  labelKey?: string;
  path: string;
}

export interface ActivityBarProps {
  navItems: NavItem[];
  pluginItems: NavItem[];
  status: StatusResponse | null;
  leftPanelOpen: boolean;
  onToggleLeftPanel: () => void;
}

// ---- Tooltip ----

type WarmRef = React.RefObject<number>;

function ActivityBarTooltip({ anchor, label, warmRef }: {
  anchor: HTMLElement;
  label: string;
  warmRef?: WarmRef;
}) {
  const rect = anchor.getBoundingClientRect();
  const isWarm = warmRef ? Date.now() - warmRef.current < 300 : false;

  useEffect(() => {
    if (warmRef) warmRef.current = Date.now();
    return () => { if (warmRef) warmRef.current = Date.now(); };
  }, [warmRef]);

  return createPortal(
    <span
      className={cn(
        "fixed z-[100] pointer-events-none",
        "px-2 py-1",
        "bg-background-base/95 border border-current/20 backdrop-blur-sm shadow-lg",
        "font-mondwest text-display text-xs tracking-[0.1em] text-midground uppercase",
      )}
      style={{
        top: rect.top + rect.height / 2,
        left: rect.right + 8,
        transform: "translateY(-50%)",
        opacity: isWarm ? 1 : undefined,
        animation: isWarm ? "none" : "sidebar-tooltip-in 120ms ease-out",
      }}
    >
      {label}
    </span>,
    document.body,
  );
}

// ---- Nav icon button ----

function NavIconButton({
  item,
  closeMobile,
  warmRef,
  t,
}: {
  item: NavItem;
  closeMobile?: () => void;
  warmRef: WarmRef;
  t: Translations;
}) {
  const { path, label, labelKey, icon: Icon } = item;
  const ref = useRef<HTMLLIElement>(null);
  const [hovered, setHovered] = useState(false);

  const navLabel = labelKey
    ? ((t.app.nav as Record<string, string>)[labelKey] ?? label)
    : label;

  return (
    <li
      ref={ref}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <NavLink
        to={path}
        end={path === "/sessions"}
        onClick={closeMobile}
        aria-label={navLabel}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
        className={({ isActive }) =>
          cn(
            "group/nav relative flex items-center justify-center",
            "w-full py-2.5",
            "transition-colors cursor-pointer",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
            isActive
              ? "text-midground"
              : "text-text-secondary hover:text-midground",
          )
        }
        style={{ clipPath: "var(--component-tab-clip-path)" }}
      >
        {({ isActive }) => (
          <>
            <Icon className="h-4 w-4 shrink-0" />

            <span
              aria-hidden
              className="absolute inset-y-0.5 inset-x-0.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/nav:opacity-5"
            />

            {isActive && (
              <span
                aria-hidden
                className="absolute left-0 top-0 bottom-0 w-0.5 bg-midground"
                style={{ mixBlendMode: "plus-lighter" }}
              />
            )}
          </>
        )}
      </NavLink>

      {hovered && ref.current && (
        <ActivityBarTooltip anchor={ref.current} label={navLabel} warmRef={warmRef} />
      )}
    </li>
  );
}

// ---- Gateway dot ----

function GatewayDot({ status, warmRef }: {
  status: StatusResponse | null;
  warmRef: WarmRef;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  const toneToColor: Record<string, string> = {
    "text-success": "bg-success",
    "text-warning": "bg-warning",
    "text-destructive": "bg-destructive",
    "text-muted-foreground": "bg-muted-foreground",
  };

  let color: string;
  let label: string;

  if (!status) {
    color = "bg-midground/20";
    label = t.status.gateway;
  } else {
    const gw = gatewayLine(status, t);
    color = toneToColor[gw.tone] ?? "bg-muted-foreground";
    label = `${t.status.gateway} ${gw.label}`;
  }

  return (
    <div
      ref={ref}
      className="flex items-center justify-center py-2"
      role="status"
      aria-label={label}
      tabIndex={0}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
    >
      <span aria-hidden className={cn("h-2 w-2 rounded-full", color)} />
      {hovered && ref.current && (
        <ActivityBarTooltip anchor={ref.current} label={label} warmRef={warmRef} />
      )}
    </div>
  );
}

// ---- System action icon ----

function SystemActionIcon({ action, icon: Icon, label, runningLabel, spin, warmRef }: {
  action: SystemAction;
  icon: ComponentType<{ className?: string }>;
  label: string;
  runningLabel: string;
  spin: boolean;
  warmRef: WarmRef;
}) {
  const navigate = useNavigate();
  const { activeAction, isBusy, isRunning, pendingAction, runAction } = useSystemActions();
  const ref = useRef<HTMLButtonElement>(null);
  const [hovered, setHovered] = useState(false);

  const isPending = pendingAction === action;
  const isActionRunning = activeAction === action && isRunning && pendingAction !== action;
  const busy = isPending || isActionRunning;
  const displayLabel = isActionRunning ? runningLabel : label;

  const handleClick = useCallback(() => {
    if (isBusy) return;
    void runAction(action);
    navigate("/sessions");
  }, [isBusy, runAction, action, navigate]);

  return (
    <li>
      <button
        ref={ref}
        onClick={handleClick}
        disabled={isBusy && !(pendingAction === action || isActionRunning)}
        aria-busy={busy}
        aria-label={displayLabel}
        type="button"
        className={cn(
          "group/action relative flex w-full items-center justify-center",
          "py-2.5",
          "transition-colors cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
          busy ? "text-midground" : "text-text-secondary hover:text-midground",
          "disabled:text-text-disabled disabled:cursor-not-allowed",
        )}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
      >
        {isPending || (isActionRunning && spin) ? (
          <Spinner className="shrink-0 text-[0.875rem]" />
        ) : (
          <Icon className={cn("h-4 w-4 shrink-0", isActionRunning && !spin && "animate-pulse")} />
        )}

        <span
          aria-hidden
          className="absolute inset-y-0.5 inset-x-0.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/action:opacity-5"
        />

        {busy && (
          <span
            aria-hidden
            className="absolute left-0 top-0 bottom-0 w-0.5 bg-midground"
            style={{ mixBlendMode: "plus-lighter" }}
          />
        )}
      </button>

      {hovered && ref.current && (
        <ActivityBarTooltip anchor={ref.current} label={displayLabel} warmRef={warmRef} />
      )}
    </li>
  );
}

// ---- Main component ----

export default function ActivityBar({
  navItems,
  pluginItems,
  status,
  leftPanelOpen,
  onToggleLeftPanel,
}: ActivityBarProps) {
  const { t } = useI18n();
  const warmRef = useRef(0);
  const panelRef = useRef<HTMLButtonElement>(null);
  const [panelHovered, setPanelHovered] = useState(false);

  const panelLabel = leftPanelOpen ? (t.common.collapse ?? "Collapse panel") : (t.common.expand ?? "Expand panel");

  return (
    <div
      className={cn(
        "hermes-activity-bar",
        "hidden lg:flex flex-col h-full w-12 shrink-0",
        "border-r border-current/20",
      )}
      style={{
        background: "var(--component-sidebar-background)",
        borderImage: "var(--component-sidebar-border-image)",
      }}
      aria-label={t.app.navigation}
    >
      {/* Panel toggle */}
      <div className="flex items-center justify-center py-2 border-b border-current/10">
        <button
          ref={panelRef}
          onClick={onToggleLeftPanel}
          aria-label={panelLabel}
          type="button"
          className="text-text-secondary hover:text-midground p-1.5 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground"
          onMouseEnter={() => setPanelHovered(true)}
          onMouseLeave={() => setPanelHovered(false)}
        >
          <PanelLeft className={cn("h-4 w-4", leftPanelOpen && "text-midground")} />
        </button>
        {panelHovered && panelRef.current && (
          <ActivityBarTooltip anchor={panelRef.current} label={panelLabel} warmRef={warmRef} />
        )}
      </div>

      {/* Navigation icons */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-1" aria-label={t.app.navigation}>
        <ul className="flex flex-col">
          {navItems.map((item) => (
            <NavIconButton key={item.path} item={item} warmRef={warmRef} t={t} />
          ))}
        </ul>

        {pluginItems.length > 0 && (
          <ul className="flex flex-col border-t border-current/10 mt-1 pt-1">
            {pluginItems.map((item) => (
              <NavIconButton key={item.path} item={item} warmRef={warmRef} t={t} />
            ))}
          </ul>
        )}
      </nav>

      {/* System actions */}
      <div className="shrink-0 border-t border-current/10 py-1">
        <ul className="flex flex-col">
          <SystemActionIcon
            action="restart"
            icon={RotateCw}
            label={t.status.restartGateway}
            runningLabel={t.status.restartingGateway}
            spin
            warmRef={warmRef}
          />
          <SystemActionIcon
            action="update"
            icon={Download}
            label={t.status.updateHermes}
            runningLabel={t.status.updatingHermes}
            spin={false}
            warmRef={warmRef}
          />
        </ul>
      </div>

      {/* Bottom: gateway dot + theme + language */}
      <div className="shrink-0 flex flex-col items-center gap-1 border-t border-current/20 py-2">
        <GatewayDot status={status} warmRef={warmRef} />

        <PluginSlot name="header-right" />

        <div className="flex flex-col items-center gap-1">
          <ThemeSwitcher collapsed dropUp />
          <LanguageSwitcher collapsed dropUp />
        </div>
      </div>
    </div>
  );
}
