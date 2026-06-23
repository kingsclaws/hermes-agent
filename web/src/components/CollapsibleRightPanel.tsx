/**
 * CollapsibleRightPanel — tabbed right panel merging ChatSidebar + CompactKanbanPanel.
 *
 * Three tabs: Info (session metadata, connection state), Kanban (swarm
 * progress), Tools (workflow buttons + tool call list).
 *
 * Owns its own GatewayClient + event WebSocket, independent of the chat
 * session.  Only connects while mounted (i.e. when the panel is expanded).
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Card } from "@nous-research/ui/ui/components/card";

import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import { LegalWorkflowPanel } from "@/components/LegalWorkflowPanel";
import { ToolCall, type ToolEntry } from "@/components/ToolCall";
import { ContextIndicator } from "@/components/ContextIndicator";
import { CompactKanbanPanel } from "@/components/CompactKanbanPanel";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { HERMES_BASE_PATH, buildWsAuthParam } from "@/lib/api";

import { cn } from "@/lib/utils";
import {
  AlertCircle,
  ChevronDown,
  Info,
  KanbanSquare,
  RefreshCw,
  Wrench,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { RightPanelTab } from "@/hooks/useRightPanel";

interface SessionInfo {
  cwd?: string;
  model?: string;
  provider?: string;
  credential_warning?: string;
}

interface RpcEnvelope {
  method?: string;
  params?: { type?: string; payload?: unknown };
}

const TOOL_LIMIT = 20;

interface KanbanEvent {
  kind: "kanban";
  eventType: string;
  taskId: string;
  title: string;
  status: string;
  worker?: string;
  summary?: string;
  timestamp: number;
}

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "idle",
  connecting: "connecting",
  open: "live",
  closed: "closed",
  error: "error",
};

const STATE_TONE: Record<
  ConnectionState,
  "secondary" | "warning" | "success" | "destructive"
> = {
  idle: "secondary",
  connecting: "warning",
  open: "success",
  closed: "secondary",
  error: "destructive",
};

const TABS: { id: RightPanelTab; label: string; Icon: typeof Info }[] = [
  { id: "info", label: "Info", Icon: Info },
  { id: "kanban", label: "Kanban", Icon: KanbanSquare },
  { id: "tools", label: "Tools", Icon: Wrench },
];

export interface CollapsibleRightPanelProps {
  channel: string;
  activeSwarmRun: { board: string; runId: string } | null;
  onSwarmClose?: () => void;
  onRunWorkflowPrompt?: (prompt: string) => void;
  activeTab: RightPanelTab;
  onTabChange: (tab: RightPanelTab) => void;
  onClose: () => void;
  className?: string;
}

export function CollapsibleRightPanel({
  channel,
  activeSwarmRun,
  onSwarmClose,
  onRunWorkflowPrompt,
  activeTab,
  onTabChange,
  onClose,
  className,
}: CollapsibleRightPanelProps) {
  // ── GatewayClient (from ChatSidebar) ──────────────────────────
  const [version, setVersion] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const gw = useMemo(() => new GatewayClient(), [version]);

  const [state, setState] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [info, setInfo] = useState<SessionInfo>({});
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [kanbanEvents, setKanbanEvents] = useState<KanbanEvent[]>([]);
  const [modelOpen, setModelOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const offState = gw.onState(setState);

    const offSessionInfo = gw.on<SessionInfo>("session.info", (ev) => {
      if (ev.session_id) setSessionId(ev.session_id);
      if (ev.payload) setInfo((prev) => ({ ...prev, ...ev.payload }));
    });

    const offError = gw.on<{ message?: string }>("error", (ev) => {
      if (ev.payload?.message) setError(ev.payload.message);
    });

    gw.connect()
      .then(() => {
        if (cancelled) return;
        return gw.request<{ session_id: string }>("session.create", {});
      })
      .then((created) => {
        if (cancelled || !created?.session_id) return;
        setSessionId(created.session_id);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });

    return () => {
      cancelled = true;
      offState();
      offSessionInfo();
      offError();
      gw.close();
    };
  }, [gw]);

  // ── Event subscriber WebSocket (from ChatSidebar) ─────────────
  useEffect(() => {
    if (!channel) return;
    let unmounting = false;
    let ws: WebSocket | null = null;
    void (async () => {
      const [authName, authValue] = await buildWsAuthParam();
      if (!authValue || unmounting) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const qs = new URLSearchParams({ [authName]: authValue, channel });
      ws = new WebSocket(
        `${proto}//${window.location.host}${HERMES_BASE_PATH}/api/events?${qs.toString()}`,
      );

      const DISCONNECTED = "events feed disconnected — tool calls may not appear";
      const surface = (msg: string) => !unmounting && setError(msg);

      ws.addEventListener("error", () => surface(DISCONNECTED));
      ws.addEventListener("close", (ev) => {
        if (ev.code === 4401 || ev.code === 4403) {
          surface(`events feed rejected (${ev.code}) — reload the page`);
        } else if (ev.code !== 1000) {
          surface(DISCONNECTED);
        }
      });

      ws.addEventListener("message", (ev) => {
        let frame: RpcEnvelope;
        try { frame = JSON.parse(ev.data); } catch { return; }
        if (frame.method !== "event" || !frame.params) return;

        const { type, payload } = frame.params;

        if (type === "tool.start") {
          const p = payload as { tool_id: string; name?: string; context?: string } | undefined;
          if (!p?.tool_id) return;
          setTools((prev) =>
            [
              ...prev,
              {
                kind: "tool" as const,
                id: `tool-${p.tool_id}-${prev.length}`,
                tool_id: p.tool_id,
                name: p?.name ?? "tool",
                context: p?.context,
                status: "running" as const,
                startedAt: Date.now(),
              },
            ].slice(-TOOL_LIMIT),
          );
        } else if (type === "tool.progress") {
          const p = payload as { name?: string; preview?: string } | undefined;
          if (!p?.name || !p.preview) return;
          setTools((prev) =>
            prev.map((t) =>
              t.status === "running" && t.name === p.name
                ? { ...t, preview: p.preview }
                : t,
            ),
          );
        } else if (type === "tool.complete") {
          const p = payload as {
            tool_id?: string; summary?: string; error?: string; inline_diff?: string;
          } | undefined;
          if (!p?.tool_id) return;
          setTools((prev) =>
            prev.map((t) =>
              t.tool_id === p.tool_id
                ? {
                    ...t,
                    status: p.error ? "error" : "done",
                    summary: p.summary,
                    error: p.error,
                    inline_diff: p.inline_diff,
                    completedAt: Date.now(),
                  }
                : t,
            ),
          );
        } else if (typeof type === "string" && type.startsWith("kanban.")) {
          const p = payload as Record<string, unknown> | undefined;
          setKanbanEvents((prev) =>
            [
              ...prev,
              {
                kind: "kanban" as const,
                eventType: type,
                taskId: (p?.task_id as string) ?? "",
                title: (p?.title as string) ?? "",
                status: (p?.status as string) ?? "",
                worker: (p?.worker as string) ?? undefined,
                summary: (p?.summary as string) ?? undefined,
                timestamp: Date.now(),
              },
            ].slice(-50),
          );
        }
      });
    })();

    return () => {
      unmounting = true;
      ws?.close();
    };
  }, [channel, version]);

  const reconnect = useCallback(() => {
    setError(null);
    setTools([]);
    setVersion((v) => v + 1);
  }, []);

  const onModelSubmit = useCallback(
    (slashCommand: string) => {
      if (!sessionId) return;
      void gw.request("slash.exec", { session_id: sessionId, command: slashCommand });
      setModelOpen(false);
    },
    [gw, sessionId],
  );

  const canPickModel = state === "open" && !!sessionId;
  const modelLabel = (info.model ?? "—").split("/").slice(-1)[0] ?? "—";
  const banner = error ?? info.credential_warning ?? null;

  // ── Render ────────────────────────────────────────────────────
  return (
    <div
      className={cn(
        "flex h-full min-w-0 flex-col overflow-hidden",
        className,
      )}
    >
      {/* Tab bar */}
      <div className="flex shrink-0 items-center border-b border-current/10 bg-background-base/60">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => onTabChange(id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs transition-colors",
              "border-b-2 -mb-px",
              activeTab === id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            <span>{label}</span>
          </button>
        ))}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto mr-1 p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/10"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Tab content */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        {activeTab === "info" && (
          <div className="flex flex-col gap-3 p-3">
            {/* Model card */}
            <Card className="flex items-center justify-between gap-2 px-3 py-2">
              <div className="min-w-0">
                <div className="text-display text-xs tracking-wider text-text-tertiary">model</div>
                <Button
                  ghost
                  size="sm"
                  disabled={!canPickModel}
                  onClick={() => setModelOpen(true)}
                  suffix={canPickModel ? <ChevronDown className="text-text-secondary" /> : undefined}
                  className="self-start min-w-0 px-0 py-0 normal-case tracking-normal text-sm font-medium hover:underline disabled:no-underline"
                  title={info.model ?? "switch model"}
                >
                  <span className="truncate">{modelLabel}</span>
                </Button>
              </div>
              <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>
            </Card>

            <ContextIndicator gw={gw} className="px-1" />

            {banner && (
              <Card className="flex items-start gap-2 border-destructive/40 bg-destructive/5 px-3 py-2 text-xs">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                <div className="min-w-0 flex-1">
                  <div className="wrap-break-word text-destructive">{banner}</div>
                  {error && (
                    <Button
                      size="sm"
                      outlined
                      className="mt-1"
                      onClick={reconnect}
                      prefix={<RefreshCw />}
                    >
                      reconnect
                    </Button>
                  )}
                </div>
              </Card>
            )}

            {/* Session info */}
            {info.cwd && (
              <Card className="px-3 py-2 text-xs text-muted-foreground">
                <span className="text-text-tertiary">cwd </span>
                <span className="font-mono">{info.cwd}</span>
              </Card>
            )}
          </div>
        )}

        {activeTab === "kanban" && (
          <div className="flex h-full flex-col">
            {activeSwarmRun ? (
              <CompactKanbanPanel
                board={activeSwarmRun.board}
                runId={activeSwarmRun.runId}
                onClose={onSwarmClose}
              />
            ) : (
              <div className="flex flex-1 items-center justify-center p-6">
                <div className="text-center text-xs text-muted-foreground">
                  <KanbanSquare className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p>No active swarm run</p>
                  <p className="mt-1 text-text-tertiary">
                    Swarm progress will appear here when a kanban workflow is running.
                  </p>
                </div>
              </div>
            )}
            {kanbanEvents.length > 0 && (
              <div className="mt-3 space-y-1 px-3">
                <div className="text-display text-xs tracking-wider text-text-tertiary mb-2">
                  live events
                </div>
                {kanbanEvents
                  .slice(-10)
                  .reverse()
                  .map((ev) => (
                    <div
                      key={`${ev.taskId}-${ev.timestamp}`}
                      className="rounded border border-current/10 px-2 py-1.5 text-xs"
                    >
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            "h-1.5 w-1.5 rounded-full shrink-0",
                            ev.status === "done" ||
                              ev.eventType === "kanban.task.completed"
                              ? "bg-green-500"
                              : ev.status === "in_progress"
                                ? "bg-blue-500 animate-pulse"
                                : ev.eventType.includes("blocked") ||
                                    ev.eventType.includes("crashed")
                                  ? "bg-red-500"
                                  : "bg-muted-foreground",
                          )}
                        />
                        <span className="font-medium truncate">{ev.title}</span>
                        <span className="text-muted-foreground shrink-0 ml-auto">
                          {ev.worker}
                        </span>
                      </div>
                      {ev.summary && (
                        <div className="mt-0.5 text-muted-foreground truncate">
                          {ev.summary}
                        </div>
                      )}
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "tools" && (
          <div className="flex flex-col gap-3 p-3">
            <LegalWorkflowPanel
              cwd={info.cwd}
              disabled={!onRunWorkflowPrompt}
              onRun={(prompt) => onRunWorkflowPrompt?.(prompt)}
            />

            <Card className="flex min-h-0 flex-col px-2 py-2">
              <div className="text-display px-1 pb-2 text-xs tracking-wider text-text-tertiary">
                tools
              </div>
              <div className="flex min-h-0 flex-col gap-1.5">
                {tools.length === 0 ? (
                  <div className="px-2 py-4 text-center text-xs text-text-secondary">
                    no tool calls yet
                  </div>
                ) : (
                  tools.map((t) => <ToolCall key={t.id} tool={t} />)
                )}
              </div>
            </Card>
          </div>
        )}
      </div>

      {/* Model picker dialog */}
      {modelOpen && canPickModel && sessionId && (
        <ModelPickerDialog
          gw={gw}
          sessionId={sessionId}
          onClose={() => setModelOpen(false)}
          onSubmit={onModelSubmit}
        />
      )}
    </div>
  );
}
