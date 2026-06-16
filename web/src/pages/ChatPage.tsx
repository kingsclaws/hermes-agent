/**
 * ChatPage — thin shell that composes:
 *   - `useChatSessionBinding` — session/project/URL binding (the effects that
 *     manage auto-resume, project selection, tab↔URL sync)
 *   - `TerminalChatHost` — xterm.js PTY terminal (WebGL, WebSocket, clipboard)
 *   - `NativeChatSurface` — structured web chat per tab
 *   - `ChatSidebar` — model/tool inspector (resizable panel or mobile sheet)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PanelRight, X } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { cn } from "@/lib/utils";

import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ResizablePanel";
import { loadPanelSize, savePanelSize } from "@/lib/layout-persistence";
import { ChatSidebar } from "@/components/ChatSidebar";
import { ChatTabBar } from "@/components/ChatTabBar";
import { useChatTabs } from "@/contexts/ChatTabContext";
import {
  dispatchWorkflowPrompt,
  NativeChatSurface,
} from "@/components/NativeChatSurface";
import {
  TerminalChatHost,
  TERMINAL_THEME,
  type TerminalChatHostHandle,
} from "@/components/TerminalChatHost";
import { useChatSessionBinding } from "@/hooks/useChatSessionBinding";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useI18n } from "@/i18n";
import { PluginSlot } from "@/plugins";
import type { ProjectInfo } from "@/lib/api";

function projectLabel(project: ProjectInfo): string {
  const client = project.client?.trim();
  return client ? `${project.name} · ${client}` : project.name;
}

export default function ChatPage({ isActive = true }: { isActive?: boolean }) {
  const { tabs, activeTabId, addTab, updateTab } = useChatTabs();
  const binding = useChatSessionBinding({ isActive, tabs, activeTabId, addTab, updateTab });
  const {
    projects, sessions, selectedProjectId,
    selectorError, selectorBusy, resumeParam, channel,
    creatingSessionRef, handleSelectProject,
  } = binding;

  const [chatMode, setChatMode] = useState<"native" | "terminal">("native");
  const terminalRef = useRef<TerminalChatHostHandle | null>(null);

  // Lazy-mount tabs: once visited, stay mounted for session persistence.
  const [mountedTabs, setMountedTabs] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (activeTabId && !mountedTabs.has(activeTabId)) {
      setMountedTabs((prev) => new Set(prev).add(activeTabId));
    }
  }, [activeTabId]);

  // ── Responsive state ──────────────────────────────────────────────
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined"
      ? window.matchMedia("(max-width: 1023px)").matches
      : false,
  );
  const [mobilePanelOpenRaw, setMobilePanelOpenRaw] = useState(false);
  const mobilePanelOpen = isActive && mobilePanelOpenRaw;
  const closeMobilePanel = useCallback(() => setMobilePanelOpenRaw(false), []);
  const { setEnd } = usePageHeader();
  const { t } = useI18n();
  const modelToolsLabel = useMemo(
    () => `${t.app.modelToolsSheetTitle} ${t.app.modelToolsSheetSubtitle}`,
    [t.app.modelToolsSheetSubtitle, t.app.modelToolsSheetTitle],
  );
  const [portalRoot] = useState<HTMLElement | null>(() =>
    typeof document !== "undefined" ? document.body : null,
  );

  useEffect(() => {
    const mql = window.matchMedia("(max-width: 1023px)");
    const sync = () => setNarrow(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (!mobilePanelOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMobilePanel();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [mobilePanelOpen, closeMobilePanel]);

  useEffect(() => {
    const mql = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setMobilePanelOpenRaw(false);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  // Page header: mobile panel toggle button.
  useEffect(() => {
    if (!isActive) { setEnd(null); return; }
    if (!narrow) { setEnd(null); return; }
    setEnd(
      <Button
        ghost
        onClick={() => setMobilePanelOpenRaw(true)}
        aria-expanded={mobilePanelOpen}
        aria-controls="chat-side-panel"
        className={cn(
          "shrink-0 rounded border border-current/20",
          "px-2 py-1 text-xs font-medium tracking-wide",
          "text-text-secondary hover:text-midground hover:bg-midground/5",
        )}
      >
        <span className="inline-flex items-center gap-1.5">
          <PanelRight className="h-3 w-3 shrink-0" />
          {modelToolsLabel}
        </span>
      </Button>,
    );
    return () => setEnd(null);
  }, [isActive, narrow, mobilePanelOpen, modelToolsLabel, setEnd]);

  // ── Workflow prompt dispatch ───────────────────────────────────────
  const handleRunWorkflowPrompt = useCallback(
    (prompt: string) => {
      if (chatMode === "native") {
        dispatchWorkflowPrompt(prompt);
        return;
      }
      const handle = terminalRef.current;
      if (!handle || !handle.isConnected()) return;
      handle.sendToTerminal(`\x1b[200~${prompt}\x1b[201~`);
      setTimeout(() => {
        if (terminalRef.current?.isConnected()) {
          terminalRef.current.sendToTerminal("\r");
        }
      }, 80);
    },
    [chatMode],
  );

  // ── Mode toggle header ────────────────────────────────────────────
  const modeToggle = (
    <span className="inline-flex shrink-0 gap-1">
      <button
        type="button"
        onClick={() => setChatMode("native")}
        className={cn(
          "rounded border px-2 py-0.5",
          chatMode === "native"
            ? "border-primary/50 bg-primary/10 text-primary"
            : "border-current/15 opacity-60 hover:opacity-100",
        )}
      >
        Native
      </button>
      <button
        type="button"
        onClick={() => setChatMode("terminal")}
        className={cn(
          "rounded border px-2 py-0.5",
          chatMode === "terminal"
            ? "border-primary/50 bg-primary/10 text-primary"
            : "border-current/15 opacity-60 hover:opacity-100",
        )}
      >
        Terminal
      </button>
    </span>
  );

  // ── Mobile model/tools sheet portal ───────────────────────────────
  const mobileModelToolsPortal =
    isActive &&
    narrow &&
    portalRoot &&
    createPortal(
      <>
        {mobilePanelOpen && (
          <Button
            ghost
            aria-label={t.app.closeModelTools}
            onClick={closeMobilePanel}
            className={cn(
              "fixed inset-0 z-[55] p-0 block",
              "bg-black/60 backdrop-blur-sm",
            )}
          />
        )}

        <div
          id="chat-side-panel"
          role="complementary"
          aria-label={modelToolsLabel}
          className={cn(
            "font-mondwest fixed top-0 right-0 z-[60] flex h-dvh max-h-dvh w-64 min-w-0 flex-col antialiased",
            "border-l border-current/20 text-midground",
            "bg-background-base/95 backdrop-blur-sm",
            "transition-transform duration-200 ease-out",
            "[background:var(--component-sidebar-background)]",
            "[clip-path:var(--component-sidebar-clip-path)]",
            "[border-image:var(--component-sidebar-border-image)]",
            mobilePanelOpen
              ? "translate-x-0"
              : "pointer-events-none translate-x-full",
          )}
        >
          <div
            className={cn(
              "flex h-14 shrink-0 items-center justify-between gap-2 border-b border-current/20 px-5",
            )}
          >
            <Typography
              mondwest
              className="text-display font-bold text-[1.125rem] leading-[0.95] tracking-[0.0525rem] text-midground"
              style={{ mixBlendMode: "plus-lighter" }}
            >
              {t.app.modelToolsSheetTitle}
              <br />
              {t.app.modelToolsSheetSubtitle}
            </Typography>

            <Button
              ghost
              size="icon"
              onClick={closeMobilePanel}
              aria-label={t.app.closeModelTools}
              className="text-text-secondary hover:text-midground"
            >
              <X />
            </Button>
          </div>

          <div
            className={cn(
              "min-h-0 flex-1 overflow-y-auto overflow-x-hidden",
              "border-t border-current/10",
            )}
          >
            <ChatSidebar
              channel={channel}
              onRunWorkflowPrompt={handleRunWorkflowPrompt}
            />
          </div>
        </div>
      </>,
      portalRoot,
    );

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <PluginSlot name="chat:top" />
      {mobileModelToolsPortal}

      <ChatTabBar />

      {/* Project selector bar */}
      <div className="hermes-desktop-pane flex shrink-0 flex-col gap-2 rounded-lg px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <label className="flex min-w-0 flex-1 items-center gap-2 text-[0.65rem] uppercase tracking-[0.14em] text-text-tertiary">
            <span className="shrink-0">Project</span>
            <select
              value={selectedProjectId}
              onChange={(event) => handleSelectProject(event.target.value)}
              className="min-w-0 flex-1 rounded border border-current/15 bg-background-base/80 px-2 py-1 text-xs normal-case tracking-normal text-text-primary outline-none hover:border-current/30 focus:border-primary/50"
            >
              <option value="">No project selected</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {projectLabel(project)}
                </option>
              ))}
            </select>
          </label>

          {selectedProjectId && (
            <span className="hidden shrink-0 items-center gap-2 text-[0.65rem] uppercase tracking-[0.14em] text-text-tertiary sm:flex">
              <span className="text-text-tertiary/40">|</span>
              {selectorBusy
                ? "loading..."
                : creatingSessionRef.current
                  ? "creating session..."
                  : (() => {
                      const s = resumeParam ? sessions.find((x) => x.id === resumeParam) : null;
                      if (s) {
                        const label = s.title || s.id.slice(0, 12);
                        return `session: ${label} · ${s.message_count}msgs`;
                      }
                      return resumeParam
                        ? `session ${resumeParam.slice(0, 8)}`
                        : "no session";
                    })()}
            </span>
          )}
        </div>

        <div className="shrink-0 text-[0.65rem] uppercase tracking-[0.14em] text-text-tertiary">
          {selectorError
            ? `ERR:${selectorError.slice(0, 30)}`
            : resumeParam
              ? `bound ${resumeParam.slice(0, 12)}`
              : sessions.length > 0
                ? `${sessions.length}sess msgs=${sessions.filter((s) => s.message_count > 0).length}`
                : selectorBusy
                  ? "loading..."
                  : "select a project"}
        </div>
      </div>

      {/* Chat body: native tabs or terminal */}
      {chatMode === "native" ? (
        <div
          className={cn(
            "hermes-desktop-pane",
            "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg",
          )}
          style={{ boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25)" }}
        >
          <div
            className={cn(
              "hermes-desktop-pane-header",
              "mb-2 flex shrink-0 items-center justify-between gap-2 rounded border",
              "px-2 py-1 text-[0.65rem] tracking-wide",
              "border-current/10 bg-background-base/70",
            )}
          >
            <span className="truncate opacity-75">
              Native Web Chat · structured legal workspace
            </span>
            {modeToggle}
          </div>

          {tabs.map((tab) => {
            const mounted = mountedTabs.has(tab.id);
            return (
              <div
                key={tab.id}
                className="flex min-h-0 min-w-0 flex-1"
                style={{ display: tab.id === activeTabId ? undefined : "none" }}
              >
                {mounted && (
                  <NativeChatSurface
                    projectContext={
                      tab.projectId
                        ? (() => {
                            const p = projects.find((pr) => pr.id === tab.projectId);
                            return p
                              ? {
                                  id: p.id,
                                  name: p.name,
                                  client: p.client,
                                  goal: p.goal,
                                  directory: p.directory,
                                  cwd: p.cwd,
                                  status: p.status,
                                }
                              : { id: tab.projectId, name: tab.projectId };
                          })()
                        : null
                    }
                    resumeTarget={tab.sessionId}
                    onSessionCreated={(sid) => {
                      if (tab.sessionId !== sid) {
                        updateTab(tab.id, { sessionId: sid });
                      }
                    }}
                  />
                )}
                {!mounted && (
                  <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">
                    Click the tab to connect…
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <ResizablePanelGroup orientation="horizontal" className="flex-1">
          <ResizablePanel
            defaultSize={loadPanelSize("chat-main")}
            minSize={30}
            onResize={(panelSize) => {
              savePanelSize("chat-main", panelSize.asPercentage);
            }}
          >
            <div className="flex min-h-0 flex-1 flex-col">
              <div
                className={cn(
                  "hermes-desktop-pane-header",
                  "mb-2 flex shrink-0 items-center justify-between gap-2 rounded border",
                  "border-white/10 bg-white/[0.035]",
                  "px-2 py-1 text-[0.65rem] tracking-wide",
                )}
                style={{ color: TERMINAL_THEME.foreground }}
              >
                <span className="truncate opacity-75">
                  Terminal fallback · PTY/TUI compatibility mode
                </span>
                {modeToggle}
              </div>

              <TerminalChatHost
                ref={terminalRef}
                isActive={isActive && chatMode === "terminal"}
                channel={channel}
                resumeParam={resumeParam}
              />
            </div>
          </ResizablePanel>

          {!narrow && (
            <>
              <ResizableHandle className="hidden lg:flex mx-0" />
              <ResizablePanel
                defaultSize={loadPanelSize("chat-sidebar")}
                minSize={15}
                maxSize={50}
              >
                <div
                  id="chat-side-panel"
                  role="complementary"
                  aria-label={modelToolsLabel}
                  className="flex min-h-0 shrink-0 flex-col overflow-hidden lg:h-full"
                >
                  <div className="min-h-0 flex-1 overflow-hidden">
                    <ChatSidebar
                      channel={channel}
                      onRunWorkflowPrompt={handleRunWorkflowPrompt}
                    />
                  </div>
                </div>
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      )}
      <PluginSlot name="chat:bottom" />
    </div>
  );
}

declare global {
  interface Window {
    __HERMES_SESSION_TOKEN__?: string;
  }
}
