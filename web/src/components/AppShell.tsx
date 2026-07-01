/**
 * AppShell — Desktop-style application shell.
 *
 * Wraps the PaneShell layout system with a SideRail (icon column),
 * Sidebar (navigation panel), RightSidebar, and StatusBar.
 *
 * Usage: <AppShell><YourContent /></AppShell>
 */

import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { PaneShell, Pane, PaneMain } from "./PaneShell";
import { SideRail, type SideRailItem } from "./SideRail";
import { syncNarrowViewport } from "@/store/layout";
import { CHAT_SIDEBAR_PANE_ID, PREVIEW_PANE_ID } from "@/store/panes";
import { api } from "@/lib/api";
import {
  MessageCircle,
  KanbanSquare,
  FileText,
  BarChart3,
  Cpu,
  Package,
  Puzzle,
  Users,
  Settings,
  KeyRound,
  BookOpen,
  Clock,
  Layers,
} from "lucide-react";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Nav config — mirrors BUILTIN_NAV_REST from App.tsx
// ---------------------------------------------------------------------------

const NAV_ITEMS: { path: string; label: string; icon: typeof MessageCircle }[] = [
  { path: "/chat", label: "Chat", icon: MessageCircle },
  { path: "/kanban", label: "Kanban", icon: KanbanSquare },
  { path: "/sessions", label: "Sessions", icon: MessageCircle },
  { path: "/analytics", label: "Analytics", icon: BarChart3 },
  { path: "/models", label: "Models", icon: Cpu },
  { path: "/logs", label: "Logs", icon: FileText },
  { path: "/cron", label: "Cron", icon: Clock },
  { path: "/skills", label: "Skills", icon: Package },
  { path: "/plugins", label: "Plugins", icon: Puzzle },
  { path: "/profiles", label: "Profiles", icon: Users },
  { path: "/config", label: "Config", icon: Settings },
  { path: "/env", label: "Keys", icon: KeyRound },
  { path: "/docs", label: "Docs", icon: BookOpen },
];

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  useEffect(() => syncNarrowViewport(), []);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden"
      style={{ background: "var(--dt-background)", color: "var(--dt-foreground)" }}
    >
      <div className="flex min-h-0 flex-1">
        <PaneShell>
          <Pane id="side-rail" side="left" defaultOpen resizable={false} defaultWidth="3rem">
            <DesktopSideRail />
          </Pane>
          <Pane id={CHAT_SIDEBAR_PANE_ID} side="left" defaultOpen hoverReveal defaultWidth="15rem">
            <DesktopSidebar />
          </Pane>
          <PaneMain>{children}</PaneMain>

          <Pane id={PREVIEW_PANE_ID} side="right" defaultOpen={false} hoverReveal defaultWidth="18rem">
            <RightPanel />
          </Pane>
        </PaneShell>
      </div>
      <DesktopStatusBar />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SideRail
// ---------------------------------------------------------------------------

const SIDE_RAIL_ITEMS: SideRailItem[] = [
  { id: "chat", icon: MessageCircle as unknown as SideRailItem["icon"], label: "Chat" },
  { id: "kanban", icon: KanbanSquare as unknown as SideRailItem["icon"], label: "Kanban" },
  { id: "files", icon: FileText as unknown as SideRailItem["icon"], label: "Files" },
];

function DesktopSideRail() {
  const nav = useNavigate();
  const loc = useLocation();
  const active = loc.pathname === "/kanban" ? "kanban" : loc.pathname.startsWith("/projects") ? "files" : "chat";

  return (
    <SideRail
      activeId={active}
      items={SIDE_RAIL_ITEMS}
      onSelect={(id) => {
        if (id === "chat") nav("/chat");
        else if (id === "kanban") nav("/kanban");
        else if (id === "files") nav("/projects");
      }}
      bottomSlot={
        <SideRail
          activeId=""
          items={[{ id: "settings", icon: Settings as unknown as SideRailItem["icon"], label: "Settings" }]}
          onSelect={() => nav("/config")}
        />
      }
    />
  );
}

// ---------------------------------------------------------------------------
// DesktopSidebar — real nav with active-route highlighting
// ---------------------------------------------------------------------------

function DesktopSidebar() {
  const loc = useLocation();
  const nav = useNavigate();
  const [sessions, setSessions] = useState<Array<{id:string;title:string;last_active?:number}>>([]);

  useEffect(() => {
    api.getSessions(20, 0).then((res: any) => {
      if (res.sessions?.length) setSessions(res.sessions.slice(0, 20));
    }).catch(() => {});
  }, []);

  const S = {
    bg: "var(--dt-sidebar)",
    border: "var(--sidebar-edge-border)",
    fg: "var(--dt-foreground)",
    textHigh: "var(--text-high)",
    textMed: "var(--text-medium)",
    textLow: "var(--text-low)",
    accent1: "var(--accent-fill-1)",
    accent2: "var(--accent-fill-2)",
    primary: "var(--theme-primary)",
  } as const;

  return (
    <div className="flex h-full flex-col overflow-hidden border-r"
      style={{ background: S.bg, borderColor: S.border, color: S.fg }}
    >
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3"
        style={{ borderColor: S.border }}
      >
        <span className="text-[0.7rem] font-semibold uppercase tracking-[0.07em]"
          style={{ color: S.textMed }}
        >Lex-Hermes</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Core nav — compact */}
        <div className="px-1.5 py-1">
          {NAV_ITEMS.slice(0, 4).map((item) => {
            const Icon = item.icon;
            const active = loc.pathname === item.path || (item.path !== "/chat" && loc.pathname.startsWith(item.path));
            return (
              <button key={item.path} type="button" onClick={() => nav(item.path)}
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors"
                style={{
                  color: active ? S.textHigh : S.textLow,
                  background: active ? S.accent2 : "transparent",
                  fontWeight: active ? 600 : 400,
                }}
                onMouseEnter={e => { if (!active) e.currentTarget.style.background = S.accent1; }}
                onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{item.label}</span>
              </button>
            );
          })}
        </div>

        {/* Sessions section */}
        {sessions.length > 0 && (
          <>
            <div className="px-3 pt-2 pb-1 text-[0.6rem] font-semibold uppercase tracking-[0.08em]"
              style={{ color: S.textLow }}
            >Recent Sessions</div>
            {sessions.slice(0, 15).map((s) => {
              const active = loc.search.includes(s.id);
              return (
                <button key={s.id} type="button" onClick={() => nav(`/chat?session=${s.id}`)}
                  className="group flex w-full items-center gap-2 rounded px-3 py-1 text-left text-xs transition-colors"
                  style={{
                    color: active ? S.textHigh : S.textLow,
                    background: active ? S.accent2 : "transparent",
                  }}
                  onMouseEnter={e => { if (!active) e.currentTarget.style.background = S.accent1; }}
                  onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
                >
                  <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: S.primary, opacity: 0.6 }}
                  />
                  <span className="truncate">{s.title || s.id.slice(0, 8)}</span>
                </button>
              );
            })}
          </>
        )}

        {/* Remaining nav items — collapsed */}
        <div className="px-3 pt-2 pb-1 text-[0.6rem] font-semibold uppercase tracking-[0.08em]"
          style={{ color: S.textLow }}
        >Tools</div>
        {NAV_ITEMS.slice(4).map((item) => {
          const Icon = item.icon;
          const active = loc.pathname.startsWith(item.path);
          return (
            <button key={item.path} type="button" onClick={() => nav(item.path)}
              className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors"
              style={{
                color: active ? S.textHigh : S.textLow,
                background: active ? S.accent2 : "transparent",
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = S.accent1; }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </div>

      <div className="shrink-0 border-t px-2 py-1.5 space-y-1"
        style={{ borderColor: S.border }}
      >
        <div className="flex items-center gap-1">
          <ThemeSwitcher collapsed={false} dropUp />
          <LanguageSwitcher collapsed={false} dropUp />
        </div>
        <div className="flex items-center gap-2 px-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: S.primary }} />
          <span className="text-[0.65rem] font-medium" style={{ color: S.textLow }}>Online</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RightSidebar
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// RightPanel — Files / Review / Preview tabs
// ---------------------------------------------------------------------------

function RightPanel() {
  const [tab, setTab] = useState<"files" | "review" | "preview">("files");
  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [projectFiles, setProjectFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.fetchProjects().then((res) => {
      if (res.projects?.length) setProjects(res.projects);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const loadProjectFiles = (projectId: string) => {
    setSelectedProject(projectId);
    setLoading(true);
    api.fetchProjectFiles(projectId).then((res: any) => {
      if (res.files?.length) setProjectFiles(res.files);
      else setProjectFiles([]);
      setLoading(false);
    }).catch(() => setLoading(false));
  };

  const S = {
    border: "var(--sidebar-edge-border)",
    high: "var(--text-high)",
    low: "var(--text-low)",
    primary: "var(--theme-primary)",
    accent1: "var(--accent-fill-1)",
  } as const;

  return (
    <div className="flex h-full flex-col overflow-hidden border-l"
      style={{ background: "var(--dt-sidebar)", borderColor: S.border, color: "var(--dt-foreground)" }}
    >
      <div className="flex h-9 shrink-0 items-end border-b px-1" style={{ borderColor: S.border }}>
        {(["files","review","preview"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className="px-2 py-1.5 text-[0.65rem] font-semibold uppercase tracking-[0.06em] capitalize"
            style={{
              color: tab === t ? S.high : S.low,
              borderColor: tab === t ? S.primary : "transparent",
              borderBottomWidth: 2, marginBottom: -1, borderBottomStyle: "solid",
            }}
          >{t}</button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "files" && (
          <>
            {loading && <div className="p-3 text-[0.68rem]" style={{color:S.low}}>Loading...</div>}
            {selectedProject ? (
              <>
                <button onClick={() => setSelectedProject("")} className="flex items-center gap-1 px-3 py-1.5 text-[0.68rem] hover:opacity-80"
                  style={{color:S.low}}>← Back to projects</button>
                {projectFiles.map((f: any) => (
                  <div key={f.path||f.name} className="flex items-center gap-2 px-3 py-1 text-[0.75rem] cursor-pointer"
                    style={{color:S.high}}
                    onMouseEnter={e => e.currentTarget.style.background=S.accent1}
                    onMouseLeave={e => e.currentTarget.style.background="transparent"}
                  ><span>📄</span><span className="truncate">{f.name||f.path}</span></div>
                ))}
                {!loading && projectFiles.length === 0 && (
                  <div className="p-4 text-center text-[0.68rem]" style={{color:S.low}}>Empty directory</div>
                )}
              </>
            ) : (
              projects.map((p) => (
                <div key={p.id||p.name} onClick={() => loadProjectFiles(p.id)}
                  className="flex items-center gap-2 px-3 py-1 text-[0.75rem] cursor-pointer"
                  style={{color:S.high}}
                  onMouseEnter={e => e.currentTarget.style.background=S.accent1}
                  onMouseLeave={e => e.currentTarget.style.background="transparent"}
                ><span>📁</span><span className="truncate">{p.name}</span></div>
              ))
            )}
          </>
        )}
        {tab === "review" && (
          <div className="p-4 text-center">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.07em]" style={{color:S.low}}>REVIEW</div>
            <div className="mt-1 text-[0.68rem]" style={{color:S.low,opacity:0.65}}>
              Select a project in the Files tab to browse documents
            </div>
          </div>
        )}
        {tab === "preview" && (
          <div className="p-4 text-center">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.07em]" style={{color:S.low}}>PREVIEW</div>
            <div className="mt-1 text-[0.68rem]" style={{color:S.low,opacity:0.65}}>
              Click a file in the Files tab to preview
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DesktopStatusBar() {
  return (
    <div className="flex h-6 shrink-0 items-center justify-between border-t px-3 text-[0.65rem]"
      style={{
        background: "var(--dt-statusbar)",
        borderColor: "var(--dt-panel-border)",
        color: "var(--text-low)",
      }}
    >
      <div className="flex items-center gap-3">
        <span style={{ fontWeight: 600, color: "var(--text-high)" }}>Lex-Hermes</span>
        <span style={{ color: "var(--accent-stroke-2)" }}>|</span>
        <span>Ready</span>
      </div>
      <div className="flex items-center gap-3">
        <span>Ln 1, Col 1</span>
      </div>
    </div>
  );
}

