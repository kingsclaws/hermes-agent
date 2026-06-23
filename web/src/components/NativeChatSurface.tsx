import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@nous-research/ui/ui/components/card";
import { ToolCall, type ToolEntry } from "@/components/ToolCall";
import { ThinkingStream, type ThinkingBlockData } from "@/components/ThinkingBlock";
import { ApprovalModal } from "@/components/ApprovalModal";
import { ContextIndicator } from "@/components/ContextIndicator";
import { CommandPalette } from "@/components/CommandPalette";
import { NotificationFeed } from "@/components/NotificationFeed";
import { Markdown } from "@/components/Markdown";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { executeSlash, parseSlash } from "@/lib/slashExec";
import { cn } from "@/lib/utils";
import { Bot, GitBranch, LoaderCircle, Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SubagentBubble, type SubagentProps } from "@/components/SubagentBubble";
import { ReasoningEffortPicker, type ReasoningEffort } from "@/components/ReasoningEffortPicker";
import { getSwarmProfile, SWARM_PROFILES } from "@/lib/swarmProfiles";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "status";
  text: string;
};

type SubagentLine = {
  id: string;
  goal: string;
  role?: string;
  profile?: string;
  icon?: string;
  color?: string;
  model?: string;
  status: "running" | "done" | "error";
  toolName?: string;
  preview?: string;
  summary?: string;
  startedAt: number;
  completedAt?: number;
};

const WORKFLOW_PROMPT_EVENT = "lex-workflow-prompt";

export function dispatchWorkflowPrompt(prompt: string) {
  window.dispatchEvent(new CustomEvent(WORKFLOW_PROMPT_EVENT, { detail: prompt }));
}

type ResumeResult = {
  session_id: string;
  resumed?: string;
  messages?: unknown[];
};

export type NativeProjectContext = {
  id: string;
  name: string;
  client?: string;
  goal?: string;
  directory?: string;
  cwd?: string;
  status?: string;
} | null;

function textFromMessage(raw: unknown): string {
  if (!raw || typeof raw !== "object") return "";
  const m = raw as Record<string, unknown>;
  const content = m.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === "object" && "text" in part
          ? String((part as { text?: unknown }).text ?? "")
          : "",
      )
      .filter(Boolean)
      .join("\n");
  }
  // session.resume returns history messages with a top-level ``text`` field
  // (no ``content`` wrapper). Tool messages use ``context``.
  const text = m.text;
  if (typeof text === "string") return text;
  const ctx = m.context;
  if (typeof ctx === "string") return ctx ? `[${m.name ?? "tool"}] ${ctx}` : "";
  return "";
}

function roleFromMessage(raw: unknown): ChatMessage["role"] | null {
  if (!raw || typeof raw !== "object") return null;
  const role = (raw as { role?: unknown }).role;
  if (role === "user" || role === "assistant") return role;
  if (role === "tool") return "status";
  return null;
}

function messagesFromResume(result: ResumeResult, fallback: string): ChatMessage[] {
  return [
    {
      id: `resume-${Date.now()}`,
      role: "status",
      text: `已恢复会话：${result.resumed ?? fallback}`,
    },
    ...(result.messages ?? [])
      .map((message, index) => {
        const role = roleFromMessage(message);
        const text = textFromMessage(message);
        return role && text
          ? { id: `history-${index}`, role, text }
          : null;
      })
      .filter((message): message is ChatMessage => message !== null),
  ];
}

export function NativeChatSurface({
  projectContext,
  resumeTarget,
  onSessionCreated,
  onSwarmLaunched,
}: {
  projectContext?: NativeProjectContext;
  resumeTarget?: string | null;
  onSessionCreated?: (sessionId: string) => void;
  onSwarmLaunched?: () => void;
}) {
  const gw = useMemo(() => new GatewayClient(), []);
  const [conn, setConn] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [subagents, setSubagents] = useState<SubagentLine[]>([]);
  const [thinkingBlocks, setThinkingBlocks] = useState<ThinkingBlockData[]>([]);
  const [input, setInput] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");

  const toSubagentProps = useCallback(
    (agent: SubagentLine): SubagentProps => ({
      id: agent.id,
      goal: agent.goal,
      role: agent.role,
      profile: agent.profile,
      model: agent.model,
      status: agent.status,
      toolName: agent.toolName,
      preview: agent.preview,
      summary: agent.summary,
      startedAt: agent.startedAt,
      completedAt: agent.completedAt,
    }),
    [],
  );
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectContextRef = useRef(projectContext);
  projectContextRef.current = projectContext;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollRefNarrow = useRef<HTMLDivElement | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const thinkingIdRef = useRef<string | null>(null);
  const queuedRef = useRef<string | null>(null);
  const [hasQueued, setHasQueued] = useState(false);
  const submitRef = useRef<(text: string) => Promise<void>>(async () => {});
  const toolNamesRef = useRef<Map<string, string>>(new Map());
  const onSwarmLaunchedRef = useRef(onSwarmLaunched);
  onSwarmLaunchedRef.current = onSwarmLaunched;

  useEffect(() => {
    let cancelled = false;
    const offState = gw.onState(setConn);
    const offStart = gw.on("message.start", () => {
      const id = `assistant-${Date.now()}`;
      assistantIdRef.current = id;
      thinkingIdRef.current = null; // Reset thinking state for new turn
      setMessages((prev) => [...prev, { id, role: "assistant", text: "" }]);
      setThinkingBlocks([]);
      setRunning(true);
      setStopping(false);
    });
    const offDelta = gw.on<{ text?: string; rendered?: string }>(
      "message.delta",
      (ev) => {
        const delta = ev.payload?.rendered ?? ev.payload?.text ?? "";
        const id = assistantIdRef.current;
        if (!id || !delta) return;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === id ? { ...msg, text: msg.text + delta } : msg,
          ),
        );
      },
    );
    const offComplete = gw.on("message.complete", () => {
      setRunning(false);
      setStopping(false);
      assistantIdRef.current = null;
      // Mark active thinking block as complete
      setThinkingBlocks((prev) =>
        prev.map((tb) => (tb.complete ? tb : { ...tb, complete: true })),
      );
    });
    const offError = gw.on<{ message?: string }>("error", (ev) => {
      setError(ev.payload?.message ?? "Agent error");
      setRunning(false);
      setStopping(false);
    });

    // Thinking / Reasoning events
    const offReasoning = gw.on<{ text?: string }>(
      "reasoning.available",
      (ev) => {
        const text = ev.payload?.text;
        if (!text) return;
        const id = `reasoning-${Date.now()}`;
        thinkingIdRef.current = id;
        setThinkingBlocks((prev) => {
          // If the last block is incomplete, append to it; otherwise create new
          const last = prev[prev.length - 1];
          if (last && !last.complete) {
            return prev.map((tb) =>
              tb.id === last.id ? { ...tb, text: tb.text + text } : tb,
            );
          }
          return [...prev, { id, text, complete: false }];
        });
      },
    );

    const offThinking = gw.on<{ text?: string }>("thinking.delta", (ev) => {
      const text = ev.payload?.text;
      if (!text) return;
      const id = thinkingIdRef.current ?? `thinking-${Date.now()}`;
      thinkingIdRef.current = id;
      setThinkingBlocks((prev) => {
        const existing = prev.find((tb) => tb.id === id);
        if (existing) {
          return prev.map((tb) =>
            tb.id === id ? { ...tb, text: tb.text + text } : tb,
          );
        }
        return [...prev, { id, text, complete: false }];
      });
    });

    const offToolStart = gw.on<{
      tool_id?: string;
      name?: string;
      context?: string;
    }>("tool.start", (ev) => {
      const id = ev.payload?.tool_id ?? `tool-${Date.now()}`;
      const name = ev.payload?.name ?? "tool";
      toolNamesRef.current.set(id, name);
      const tool: ToolEntry = {
        kind: "tool",
        id,
        tool_id: id,
        name: ev.payload?.name ?? "tool",
        context: ev.payload?.context,
        status: "running",
        startedAt: Date.now(),
      };
      setTools((prev) => [...prev, tool].slice(-30));
    });

    const offToolComplete = gw.on<{
      tool_id?: string;
      summary?: string;
      error?: string;
      inline_diff?: string;
    }>("tool.complete", (ev) => {
      const id = ev.payload?.tool_id;
      if (!id) return;
      setTools((prev) =>
        prev.map((tool) =>
          tool.id === id
            ? {
                ...tool,
                status: ev.payload?.error ? "error" : "done",
                summary: ev.payload?.summary,
                error: ev.payload?.error,
                inline_diff: ev.payload?.inline_diff,
                completedAt: Date.now(),
              }
            : tool,
        ),
      );
      const toolName = toolNamesRef.current.get(id);
      if (
        !ev.payload?.error &&
        toolName &&
        (toolName === "legal_orchestrate" || toolName === "compile_swarm_workflow")
      ) {
        onSwarmLaunchedRef.current?.();
      }
    });

    const updateSubagent = (
      payload: Record<string, unknown>,
      eventType: string,
    ) => {
      const id = String(
        payload.subagent_id ?? payload.id ?? `subagent-${Date.now()}`,
      );
      const status =
        eventType === "subagent.complete"
          ? payload.status === "error"
            ? "error"
            : "done"
          : payload.status === "error"
            ? "error"
            : "running";
      const text = String(
        payload.text ?? payload.tool_preview ?? payload.preview ?? "",
      );

      const profileHint =
        (typeof payload.profile === "string" ? payload.profile : null) ??
        (typeof payload.role === "string" ? payload.role : null) ??
        "";
      const meta = getSwarmProfile(profileHint);

      setSubagents((prev) => {
        const existing = prev.find((item) => item.id === id);
        const next: SubagentLine = {
          id,
          goal: String(payload.goal ?? existing?.goal ?? "delegated task"),
          role: payload.role ? String(payload.role) : existing?.role,
          profile: meta.id !== "unknown" ? meta.id : (existing?.profile ?? profileHint),
          model: payload.model ? String(payload.model) : existing?.model,
          status,
          toolName: payload.tool_name
            ? String(payload.tool_name)
            : existing?.toolName,
          preview: text || existing?.preview,
          summary: payload.summary
            ? String(payload.summary)
            : existing?.summary,
          startedAt: existing?.startedAt ?? Date.now(),
          completedAt:
            status === "running" ? existing?.completedAt : Date.now(),
        };
        const merged = existing
          ? prev.map((item) => (item.id === id ? next : item))
          : [...prev, next];
        return merged.slice(-20);
      });
    };

    const offToolProgress = gw.on<Record<string, unknown>>(
      "tool.progress",
      (ev) => {
        const payload = ev.payload ?? {};
        const eventType = String(payload.event_type ?? "");
        if (eventType.startsWith("subagent.") || payload.subagent_id) {
          updateSubagent(payload, eventType || "subagent.progress");
          return;
        }
        const name =
          typeof payload.name === "string" ? payload.name : null;
        const preview =
          typeof payload.preview === "string" ? payload.preview : "";
        if (!name || !preview) return;
        setTools((prev) =>
          prev.map((tool) =>
            tool.status === "running" && tool.name === name
              ? { ...tool, preview }
              : tool,
          ),
        );
      },
    );

    const offSubagentStart = gw.on<Record<string, unknown>>(
      "subagent.start",
      (ev) => {
        updateSubagent(ev.payload ?? {}, "subagent.start");
      },
    );
    const offSubagentTool = gw.on<Record<string, unknown>>(
      "subagent.tool",
      (ev) => {
        updateSubagent(ev.payload ?? {}, "subagent.tool");
      },
    );
    const offSubagentComplete = gw.on<Record<string, unknown>>(
      "subagent.complete",
      (ev) => {
        updateSubagent(ev.payload ?? {}, "subagent.complete");
      },
    );

    gw.connect()
      .then(() => {
        if (cancelled) return null;

        const swarmHint = {
          _swarm: {
            enabled: true,
            profiles: SWARM_PROFILES.map((p) => ({
              id: p.id,
              name: p.name,
              role: p.role,
              description: p.description,
            })),
          },
        };

        if (resumeTarget) {
          return gw.request<ResumeResult>("session.resume", {
            session_id: resumeTarget,
            project_context: { ...swarmHint, ...(projectContextRef.current ?? {}) },
            swarm: swarmHint,
          }).catch((e: Error) => {
            // Stale session from localStorage — fall back to creating a fresh one.
            if (/not found/i.test(e.message)) {
              return gw.request<{ session_id: string }>("session.create", {
                project_context: { ...swarmHint, ...(projectContextRef.current ?? {}) },
                swarm: swarmHint,
              });
            }
            throw e;
          });
        }
        return gw.request<{ session_id: string }>("session.create", {
          project_context: { ...swarmHint, ...(projectContextRef.current ?? {}) },
          swarm: swarmHint,
        });
      })
      .then((created) => {
        if (cancelled || !created?.session_id) return;
        setSessionId(created.session_id);
        if ("messages" in created) {
          const resumed = created as ResumeResult;
          setMessages(messagesFromResume(
            {
              ...resumed,
              messages: Array.isArray(resumed.messages) ? resumed.messages : [],
            },
            resumeTarget ?? created.session_id,
          ));
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });

    return () => {
      cancelled = true;
      offState();
      offStart();
      offDelta();
      offComplete();
      offError();
      offReasoning();
      offThinking();
      offToolStart();
      offToolComplete();
      offToolProgress();
      offSubagentStart();
      offSubagentTool();
      offSubagentComplete();
      gw.close();
    };
  }, [gw, resumeTarget]);

  // Propagate sessionId to parent (for per-tab persistence).
  useEffect(() => {
    if (sessionId && onSessionCreated) onSessionCreated(sessionId);
  }, [sessionId, onSessionCreated]);

  // Auto-submit queued message when the current turn finishes.
  useEffect(() => {
    if (!running && queuedRef.current) {
      const text = queuedRef.current;
      queuedRef.current = null;
      setHasQueued(false);
      setTimeout(() => submitRef.current(text), 0);
    }
  }, [running]);

  useEffect(() => {
    const el = scrollRef.current ?? scrollRefNarrow.current;
    el?.scrollTo({
      top: el.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, tools, thinkingBlocks]);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (!sessionId) {
        setInput(trimmed);
        setError(
          "Chat is still connecting. Submit again once the status is open.",
        );
        return;
      }
      if (running) {
        setInput(trimmed);
        queuedRef.current = trimmed;
        setHasQueued(true);
        setError(null);
        return;
      }

      if (trimmed.startsWith("/")) {
        const { name, arg } = parseSlash(trimmed);
        setError(null);
        setInput("");
        setMessages((prev) => [
          ...prev,
          { id: `slash-${Date.now()}`, role: "user", text: trimmed },
        ]);

        if (name === "resume" && arg) {
          try {
            const resumed = await gw.request<{
              session_id: string;
              resumed?: string;
              messages?: unknown[];
            }>("session.resume", {
              session_id: arg,
              project_context: projectContext,
            });
            setSessionId(resumed.session_id);
            setMessages(messagesFromResume(resumed, arg));
            return;
          } catch (e) {
            setMessages((prev) => [
              ...prev,
              {
                id: `resume-error-${Date.now()}`,
                role: "status",
                text: `原生恢复失败，尝试 slash fallback：${
                  e instanceof Error ? e.message : String(e)
                }`,
              },
            ]);
          }
        }

        if (name === "queue" && arg) {
          setInput("");
          queuedRef.current = arg;
          setHasQueued(true);
          setMessages((prev) => [
            ...prev,
            {
              id: `queued-${Date.now()}`,
              role: "status",
              text: `已排队消息，当前轮次完成后自动提交：${arg.slice(0, 80)}${arg.length > 80 ? "…" : ""}`,
            },
          ]);
          return;
        }

        await executeSlash({
          command: trimmed,
          sessionId,
          gw,
          callbacks: {
            sys: (body) =>
              setMessages((prev) => [
                ...prev,
                { id: `system-${Date.now()}`, role: "status", text: body },
              ]),
            send: (message) => submit(message),
          },
        });
        return;
      }

      setError(null);
      setMessages((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: "user", text: trimmed },
      ]);
      setInput("");
      setRunning(true);
      try {
        await gw.request("prompt.submit", {
          session_id: sessionId,
          text: trimmed,
          project_context: projectContext,
          ...(reasoningEffort !== "auto" && { reasoning_effort: reasoningEffort }),
        });
      } catch (e) {
        setRunning(false);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [gw, projectContext, running, sessionId],
  );
  submitRef.current = submit;

  const interrupt = useCallback(async () => {
    if (!sessionId || !running || stopping) return;
    try {
      setStopping(true);
      await gw.request(
        "session.interrupt",
        { session_id: sessionId },
        10_000,
      );
      setMessages((prev) => [
        ...prev,
        {
          id: `interrupt-${Date.now()}`,
          role: "status",
          text: "已请求停止当前任务。",
        },
      ]);
    } catch (e) {
      setStopping(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [gw, running, sessionId, stopping]);

  const interruptSubagent = useCallback(
    async (subagentId: string) => {
      try {
        await gw.request(
          "subagent.interrupt",
          { subagent_id: subagentId },
          10_000,
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [gw],
  );

  const handleCommandPalette = useCallback(
    (command: string) => {
      void submit(command);
    },
    [submit],
  );

  useEffect(() => {
    const handler = (ev: Event) => {
      const prompt = (ev as CustomEvent<string>).detail;
      if (typeof prompt === "string") void submit(prompt);
    };
    window.addEventListener(WORKFLOW_PROMPT_EVENT, handler);
    return () => window.removeEventListener(WORKFLOW_PROMPT_EVENT, handler);
  }, [submit]);

  return (
    <Card className="flex min-h-0 flex-1 flex-col overflow-hidden border-primary/20 bg-background-base/70 p-0 normal-case">
      {/* Header bar */}
      <div className="flex items-center justify-between gap-2 border-b border-current/10 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            native legal chat
          </div>
          <div className="flex items-center gap-3">
            <div className="truncate text-sm text-muted-foreground">
              JSON-RPC session · tools and workflow rendered by Web UI
            </div>
            {projectContext && (
              <div
                className="hidden max-w-[18rem] truncate rounded border border-primary/25 bg-primary/5 px-2 py-0.5 text-[0.65rem] text-primary sm:block"
                title={projectContext.directory || projectContext.cwd || projectContext.name}
              >
                {projectContext.name}
              </div>
            )}
            {sessionId && (
              <div className="hidden truncate font-mono-ui text-[0.65rem] text-muted-foreground/70 sm:block">
                {sessionId}
              </div>
            )}
            <ContextIndicator gw={gw} />
          </div>
        </div>
        <span
          className={cn(
            "rounded border px-2 py-0.5 text-[0.65rem] shrink-0",
            conn === "open"
              ? "border-success/40 text-success"
              : "border-current/20 text-muted-foreground",
          )}
        >
          {conn}
        </span>
      </div>

      {/* Main content area — resizable split on xl, vertical stack on narrow */}
      <div className="min-h-0 flex-1 grid-cols-1 overflow-hidden xl:hidden">
        <div
          ref={scrollRefNarrow}
          className="min-h-0 space-y-3 overflow-y-auto px-3 py-3"
        >
          {messages.length === 0 && (
            <div className="rounded border border-dashed border-current/15 p-4 text-sm text-muted-foreground">
              这里是原生 Web Chat，不是 TUI。右侧 workflow 操作会直接提交到这个会话；delegate 和工具执行在右侧检查器里显示。
              <br />
              <span className="text-xs text-muted-foreground/60 mt-1 block">
                Cmd+K / Ctrl+K to open command palette
              </span>
            </div>
          )}

          {(() => {
            const lastUserIdx = messages.reduce(
              (acc, m, i) => (m.role === "user" ? i : acc),
              -1,
            );
            return messages.map((message, idx) => (
              <div key={message.id}>
                {message.role === "assistant" &&
                  thinkingBlocks.length > 0 &&
                  message.id ===
                    messages.filter((m) => m.role === "assistant").slice(-1)[0]
                      ?.id && (
                    <ThinkingStream blocks={thinkingBlocks} className="mb-2" />
                  )}

                <div
                  className={cn(
                    "max-w-[92%] rounded-lg border px-3 py-2 text-sm leading-6",
                    message.role === "user"
                      ? "ml-auto border-primary/30 bg-primary/10"
                      : message.role === "status"
                        ? "mx-auto border-current/10 bg-muted/10 text-xs text-muted-foreground"
                        : "mr-auto border-current/10 bg-black/10",
                  )}
                >
                  {message.role === "assistant" && message.text ? (
                    <Markdown
                      content={message.text}
                      streaming={running &&
                        message.id === assistantIdRef.current}
                    />
                  ) : message.text || message.role === "assistant" ? (
                    message.text || "…"
                  ) : null}
                </div>

                {idx === lastUserIdx && subagents.length > 0 && (
                  <div className="mt-1 space-y-1">
                    {subagents.map((agent) => (
                      <SubagentBubble
                        key={agent.id}
                        agent={toSubagentProps(agent)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ));
          })()}
        </div>
      </div>

      <div className="hidden min-h-0 flex-1 xl:flex">
        <div
          ref={scrollRef}
          className="min-h-0 min-w-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
        >
          {messages.length === 0 && (
            <div className="rounded border border-dashed border-current/15 p-4 text-sm text-muted-foreground">
              这里是原生 Web Chat，不是 TUI。右侧 workflow 操作会直接提交到这个会话；delegate 和工具执行在右侧检查器里显示。
              <br />
              <span className="text-xs text-muted-foreground/60 mt-1 block">
                Cmd+K / Ctrl+K to open command palette
              </span>
            </div>
          )}

          {(() => {
            const lastUserIdx = messages.reduce(
              (acc, m, i) => (m.role === "user" ? i : acc),
              -1,
            );
            return messages.map((message, idx) => (
              <div key={message.id}>
                {message.role === "assistant" &&
                  thinkingBlocks.length > 0 &&
                  message.id ===
                    messages.filter((m) => m.role === "assistant").slice(-1)[0]
                      ?.id && (
                    <ThinkingStream blocks={thinkingBlocks} className="mb-2" />
                  )}

                <div
                  className={cn(
                    "max-w-[92%] rounded-lg border px-3 py-2 text-sm leading-6",
                    message.role === "user"
                      ? "ml-auto border-primary/30 bg-primary/10"
                      : message.role === "status"
                        ? "mx-auto border-current/10 bg-muted/10 text-xs text-muted-foreground"
                        : "mr-auto border-current/10 bg-black/10",
                  )}
                >
                  {message.role === "assistant" && message.text ? (
                    <Markdown
                      content={message.text}
                      streaming={running &&
                        message.id === assistantIdRef.current}
                    />
                  ) : message.text || message.role === "assistant" ? (
                    message.text || "…"
                  ) : null}
                </div>

                {idx === lastUserIdx && subagents.length > 0 && (
                  <div className="mt-1 space-y-1">
                    {subagents.map((agent) => (
                      <SubagentBubble
                        key={agent.id}
                        agent={toSubagentProps(agent)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ));
          })()}
        </div>

        <div className="min-h-0 w-80 shrink-0 border-l border-current/10">
          <ExecutionInspector
            running={running}
            stopping={stopping}
            tools={tools}
            subagents={subagents}
            onInterruptTurn={interrupt}
            onInterruptSubagent={interruptSubagent}
          />
        </div>
      </div>

      {/* Compact inspector for narrow viewports */}
      <div className="border-t border-current/10 px-3 py-2 xl:hidden">
        <ExecutionInspector
          compact
          running={running}
          stopping={stopping}
          tools={tools}
          subagents={subagents}
          onInterruptTurn={interrupt}
          onInterruptSubagent={interruptSubagent}
        />
      </div>

      {/* Error banner */}
      {error && (
        <div className="border-t border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Queue indicator */}
      {hasQueued && (
        <div className="flex items-center gap-2 border-t border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-xs">
          <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-amber-400" />
          <span className="flex-1 text-amber-300/80">
            Message queued — will submit when current turn completes.{" "}
            {queuedRef.current && (
              <span className="text-muted-foreground/60">
                ({queuedRef.current.slice(0, 50)}
                {queuedRef.current.length > 50 ? "…" : ""})
              </span>
            )}
          </span>
          <button
            type="button"
            onClick={() => {
              queuedRef.current = null;
              setHasQueued(false);
            }}
            className="shrink-0 rounded border border-current/20 px-1.5 py-0.5 text-[0.65rem] hover:bg-amber-500/10"
          >
            cancel
          </button>
        </div>
      )}

      {/* Composer */}
      <form
        className="flex shrink-0 flex-col gap-1 border-t border-current/10 p-3"
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit(input);
        }}
      >
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(ev) => setInput(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                void submit(input);
              }
            }}
            disabled={!sessionId || conn !== "open"}
            rows={2}
            placeholder="输入法律工作指令。Shift+Enter 换行。Cmd+K 命令面板。"
            className="min-h-12 flex-1 resize-none rounded border border-current/15 bg-black/10 px-3 py-2 text-sm outline-none focus:border-primary/60"
          />
          <Button
            type={running ? "button" : "submit"}
            onClick={running ? interrupt : undefined}
            disabled={!sessionId || stopping || (!running && !input.trim())}
            title={running ? "Stop current turn" : "Send"}
            aria-label={running ? "Stop current turn" : "Send"}
            className="self-end px-3"
          >
            {running ? (
              <Square className="h-4 w-4" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <ReasoningEffortPicker
            value={reasoningEffort}
            onChange={setReasoningEffort}
            disabled={!sessionId || conn !== "open"}
          />
        </div>
      </form>

      {/* Overlays */}
      <ApprovalModal gw={gw} sessionId={sessionId} />
      <CommandPalette
        gw={gw}
        sessionId={sessionId}
        onExecute={handleCommandPalette}
      />
      <NotificationFeed gw={gw} />
    </Card>
  );
}

function ExecutionInspector({
  compact,
  running,
  stopping,
  tools,
  subagents,
  onInterruptTurn,
  onInterruptSubagent,
}: {
  compact?: boolean;
  running: boolean;
  stopping: boolean;
  tools: ToolEntry[];
  subagents: SubagentLine[];
  onInterruptTurn: () => void;
  onInterruptSubagent: (subagentId: string) => void;
}) {
  const runningTools = tools.filter(
    (tool) => tool.status === "running",
  ).length;
  const runningSubagents = subagents.filter(
    (agent) => agent.status === "running",
  ).length;

  return (
    <aside
      className={cn(
        "min-h-0 border-current/10 bg-black/[0.08]",
        compact
          ? "max-h-64 overflow-y-auto rounded border p-2"
          : "hidden overflow-y-auto border-l p-3 xl:block",
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider">
            <GitBranch className="h-3.5 w-3.5 text-primary" />
            Execution
          </div>
          <div className="text-[0.65rem] text-muted-foreground">
            {stopping ? "stopping" : running ? "turn running" : "idle"} · {runningSubagents} agents ·{" "}
            {runningTools} tools
          </div>
        </div>
        {running && (
          <Button
            type="button"
            size="sm"
            onClick={onInterruptTurn}
            disabled={stopping}
            className="h-7 shrink-0 px-2 text-[0.7rem]"
          >
            {stopping ? "stopping" : "stop turn"}
          </Button>
        )}
      </div>

      {subagents.length === 0 && tools.length === 0 ? (
        <div className="rounded border border-dashed border-current/15 p-3 text-xs text-muted-foreground">
          工具调用、delegate task、子 agent 状态会显示在这里，不再混在合同分析正文里。
        </div>
      ) : null}

      {subagents.length > 0 && (
        <section className="mb-4 space-y-2">
          <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            delegated agents
          </div>
          {subagents
            .slice()
            .reverse()
            .map((agent) => (
              <SubagentCard
                key={agent.id}
                agent={agent}
                onInterrupt={() => onInterruptSubagent(agent.id)}
              />
            ))}
        </section>
      )}

      {tools.length > 0 && (
        <section className="space-y-2">
          <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
            native tools
          </div>
          {tools
            .slice()
            .reverse()
            .map((tool) => (
              <ToolCall key={tool.id} tool={tool} />
            ))}
        </section>
      )}
    </aside>
  );
}

function SubagentCard({
  agent,
  onInterrupt,
}: {
  agent: SubagentLine;
  onInterrupt: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-2 text-xs",
        agent.status === "running"
          ? "border-primary/40 bg-primary/[0.04]"
          : agent.status === "error"
            ? "border-destructive/50 bg-destructive/[0.04]"
            : "border-current/10 bg-muted/10",
      )}
    >
      <div className="flex items-start gap-2">
        <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 font-medium">{agent.goal}</div>
          <div className="mt-1 flex flex-wrap gap-1 text-[0.65rem] text-muted-foreground">
            {agent.role && <span>{agent.role}</span>}
            {agent.model && <span>{agent.model}</span>}
            {agent.toolName && <span>tool: {agent.toolName}</span>}
          </div>
        </div>
        {agent.status === "running" && (
          <Button
            type="button"
            size="sm"
            className="h-6 px-2"
            onClick={onInterrupt}
          >
            stop
          </Button>
        )}
      </div>
      {(agent.preview || agent.summary) && (
        <div className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap rounded bg-black/10 p-2 font-mono text-[0.7rem] text-muted-foreground">
          {agent.summary ?? agent.preview}
        </div>
      )}
    </div>
  );
}
