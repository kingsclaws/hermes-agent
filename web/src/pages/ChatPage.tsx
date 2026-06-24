/**
 * ChatPage — thin shell that composes:
 *   - `useChatSessionBinding` — session/project/URL binding (the effects that
 *     manage auto-resume, project selection, tab↔URL sync)
 *   - `NativeChatSurface` — structured web chat per tab
 *   - `CollapsibleRightPanel` — tabbed info/kanban/tools panel (collapsed by default)
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { PanelRight, X } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@nous-research/ui/ui/components/typography/index";
import { cn } from "@/lib/utils";

import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ResizablePanel";
import { loadPanelSize, savePanelSize } from "@/lib/layout-persistence";
import { CollapsibleRightPanel } from "@/components/CollapsibleRightPanel";
import { WelcomePanel } from "@/components/WelcomePanel";
import { ChatTopBar } from "@/components/ChatTopBar";
import { useChatTabs } from "@/contexts/ChatTabContext";
import {
  dispatchWorkflowPrompt,
  NativeChatSurface,
} from "@/components/NativeChatSurface";
import { useChatSessionBinding } from "@/hooks/useChatSessionBinding";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useI18n } from "@/i18n";
import { PluginSlot } from "@/plugins";
import { api } from "@/lib/api";
import type { ReasoningEffort } from "@/components/ReasoningEffortPicker";
import { useRightPanel } from "@/hooks/useRightPanel";

export default function ChatPage({ isActive = true }: { isActive?: boolean }) {
  const { tabs, activeTabId, addTab, updateTab, findOrCreateTab } = useChatTabs();
  const binding = useChatSessionBinding({ isActive, tabs, activeTabId, addTab, updateTab, findOrCreateTab });
  const {
    projects, sessions, selectedProjectId,
    resumeParam, channel, handleSelectProject,
  } = binding;

  // Lazy-mount tabs: once visited, stay mounted for session persistence.
  const [mountedTabs, setMountedTabs] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (activeTabId && !mountedTabs.has(activeTabId)) {
      setMountedTabs((prev) => new Set(prev).add(activeTabId));
    }
  }, [activeTabId]);

  const [activeSwarmRun, setActiveSwarmRun] = useState<{ board: string; runId: string } | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");
  const {
    expanded: rightPanelOpen,
    activeTab: rightPanelTab,
    toggle: toggleRightPanel,
    expand: expandRightPanel,
    changeTab: changeRightPanelTab,
  } = useRightPanel();

  useEffect(() => {
    if (!selectedProjectId) {
      setActiveSwarmRun(null);
      return;
    }
    let cancelled = false;
    api.fetchAllSwarmRuns().then((res) => {
      if (cancelled || !res.ok) return;
      const active = res.runs.find(
        (r) => r.status === "running" || r.status === "ready",
      );
      if (active) {
        setActiveSwarmRun({ board: active.board, runId: active.run_id });
        expandRightPanel();
        changeRightPanelTab("kanban");
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [selectedProjectId, expandRightPanel, changeRightPanelTab]);

  const handleSwarmLaunched = useCallback(() => {
    if (!selectedProjectId) return;
    api.fetchAllSwarmRuns().then((res) => {
      if (!res.ok) return;
      const active = res.runs.find(
        (r) => r.status === "running" || r.status === "ready",
      );
      if (active) {
        setActiveSwarmRun({ board: active.board, runId: active.run_id });
        expandRightPanel();
        changeRightPanelTab("kanban");
      }
    }).catch(() => {});
  }, [selectedProjectId, expandRightPanel, changeRightPanelTab]);

  // Stable project-context objects: only changes when `projects` refetches,
  // not on every render.  Prevents NativeChatSurface re-renders.
  const projectContextCache = useMemo(() => {
    const cache: Record<string, { id: string; name: string; client?: string; goal?: string; directory?: string; cwd?: string; status?: string }> = {};
    for (const p of projects) {
      cache[p.id] = { id: p.id, name: p.name, client: p.client, goal: p.goal, directory: p.directory, cwd: p.cwd, status: p.status };
    }
    return cache;
  }, [projects]);

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
      dispatchWorkflowPrompt(prompt);
    },
    [],
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
            <CollapsibleRightPanel
              channel={channel}
              activeSwarmRun={activeSwarmRun}
              onSwarmClose={() => setActiveSwarmRun(null)}
              onRunWorkflowPrompt={handleRunWorkflowPrompt}
              activeTab={rightPanelTab}
              onTabChange={changeRightPanelTab}
              onClose={closeMobilePanel}
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

      <ChatTopBar
        selectedProjectId={selectedProjectId}
        projects={projects}
        onSelectProject={handleSelectProject}
        sessionTitle={(() => {
          const s = resumeParam ? sessions.find((x) => x.id === resumeParam) : null;
          return s ? (s.title || s.id.slice(0, 12)) : null;
        })()}
        reasoningEffort={reasoningEffort}
        onReasoningEffortChange={setReasoningEffort}
        effortDisabled={!resumeParam}
        rightPanelOpen={rightPanelOpen}
        onToggleRightPanel={toggleRightPanel}
        narrow={narrow}
      />

      {/* Chat body */}
      <div className="flex min-h-0 flex-1 gap-0 overflow-hidden">
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel
            defaultSize={loadPanelSize("chat-native-main")}
            minSize={40}
            onResize={(panelSize) => {
              savePanelSize("chat-native-main", panelSize.asPercentage);
            }}
          >
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
              </div>

              {(() => {
                const activeTab = tabs.find((t) => t.id === activeTabId);
                const showWelcome = !activeTab?.sessionId && !activeTab?.projectId;

                if (showWelcome) {
                  return <WelcomePanel key="welcome" />;
                }

                return tabs.map((tab) => {
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
                              ? projectContextCache[tab.projectId] ?? { id: tab.projectId, name: tab.projectId }
                              : null
                          }
                          resumeTarget={tab.sessionId}
                          onSessionCreated={(sid) => {
                            if (tab.sessionId !== sid) {
                              updateTab(tab.id, { sessionId: sid });
                            }
                          }}
                          onSwarmLaunched={handleSwarmLaunched}
                          reasoningEffort={reasoningEffort}
                        />
                      )}
                      {!mounted && (
                        <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">
                          Click the tab to connect…
                        </div>
                      )}
                    </div>
                  );
                });
              })()}
            </div>
          </ResizablePanel>

          {rightPanelOpen && (
            <>
              <ResizableHandle className="mx-0" />
              <ResizablePanel
                defaultSize={loadPanelSize("right-panel")}
                minSize={20}
                maxSize={45}
                onResize={(panelSize) => {
                  savePanelSize("right-panel", panelSize.asPercentage);
                }}
              >
                <CollapsibleRightPanel
                  channel={channel}
                  activeSwarmRun={activeSwarmRun}
                  onSwarmClose={() => setActiveSwarmRun(null)}
                  onRunWorkflowPrompt={handleRunWorkflowPrompt}
                  activeTab={rightPanelTab}
                  onTabChange={changeRightPanelTab}
                  onClose={toggleRightPanel}
                />
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      </div>
      <PluginSlot name="chat:bottom" />
    </div>
  );
}

declare global {
  interface Window {
    __HERMES_SESSION_TOKEN__?: string;
  }
}
