import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@nous-research/ui/ui/components/card";
import type { ToolEntry } from "@/components/ToolCall";
import type { ThinkingBlockData } from "@/components/ThinkingBlock";
import { ApprovalModal } from "@/components/ApprovalModal";
import { ContextIndicator } from "@/components/ContextIndicator";
import { CommandPalette } from "@/components/CommandPalette";
import { NotificationFeed } from "@/components/NotificationFeed";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { uploadChatAttachments, type ChatAttachmentUpload } from "@/lib/api";
import { executeSlash, parseSlash } from "@/lib/slashExec";
import { cn } from "@/lib/utils";
import { FileText, Image as ImageIcon, ListPlus, LoaderCircle, Paperclip, Send, Square, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReasoningEffort } from "@/components/ReasoningEffortPicker";
import { getSwarmProfile, SWARM_PROFILES } from "@/lib/swarmProfiles";
import { ChatMessageList } from "@/components/ChatMessageList";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "status";
  text: string;
  timestamp: number;
  senderName?: string;
};

export type { ChatMessage };

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

export type { SubagentLine };

const WORKFLOW_PROMPT_EVENT = "lex-workflow-prompt";

type DraftAttachment = {
  id: string;
  file: File;
  previewUrl?: string;
};

type QueuedPrompt = {
  text: string;
  attachments: DraftAttachment[];
};

export function dispatchWorkflowPrompt(prompt: string) {
  window.dispatchEvent(new CustomEvent(WORKFLOW_PROMPT_EVENT, { detail: prompt }));
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
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
  const now = Date.now();
  return [
    {
      id: `resume-${now}`,
      role: "status",
      text: `已恢复会话：${result.resumed ?? fallback}`,
      timestamp: now,
    },
    ...(result.messages ?? [])
      .map((message, index) => {
        const role = roleFromMessage(message);
        const text = textFromMessage(message);
        return role && text
          ? { id: `history-${index}`, role, text, timestamp: now }
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
  reasoningEffort,
}: {
  projectContext?: NativeProjectContext;
  resumeTarget?: string | null;
  onSessionCreated?: (sessionId: string) => void;
  onSwarmLaunched?: () => void;
  reasoningEffort: ReasoningEffort;
}) {
  const gw = useMemo(() => new GatewayClient(), []);
  const [conn, setConn] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [subagents, setSubagents] = useState<SubagentLine[]>([]);
  const swarmState = useMemo<"idle" | "active" | "completed">(() => {
    if (subagents.length === 0) return "idle";
    if (subagents.some((a) => a.status === "running")) return "active";
    return "completed";
  }, [subagents]);
  const [thinkingBlocks, setThinkingBlocks] = useState<ThinkingBlockData[]>([]);
  const [input, setInput] = useState("");

  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectContextRef = useRef(projectContext);
  projectContextRef.current = projectContext;
  const onSessionCreatedRef = useRef(onSessionCreated);
  onSessionCreatedRef.current = onSessionCreated;
  const assistantIdRef = useRef<string | null>(null);
  const thinkingIdRef = useRef<string | null>(null);
  const queuedRef = useRef<QueuedPrompt | null>(null);
  const [hasQueued, setHasQueued] = useState(false);
  const submitRef = useRef<(text: string, draftAttachments?: DraftAttachment[]) => Promise<void>>(async () => {});
  const toolNamesRef = useRef<Map<string, string>>(new Map());
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const onSwarmLaunchedRef = useRef(onSwarmLaunched);
  onSwarmLaunchedRef.current = onSwarmLaunched;
  const projectContextKey = useMemo(() => {
    if (!projectContext) return "no-project";
    return [
      projectContext.id,
      projectContext.name,
      projectContext.directory ?? projectContext.cwd ?? "",
    ].join("\u0001");
  }, [projectContext]);

  useEffect(() => {
    let cancelled = false;
    setDraftAttachments((prev) => {
      prev.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
      return [];
    });
    setSessionId(null);
    setMessages([]);
    setTools([]);
    setSubagents([]);
    setThinkingBlocks([]);
    setError(null);
    setRunning(false);
    setStopping(false);
    assistantIdRef.current = null;
    thinkingIdRef.current = null;
    toolNamesRef.current.clear();
    const offState = gw.onState(setConn);
    const offStart = gw.on("message.start", () => {
      const id = `assistant-${Date.now()}`;
      assistantIdRef.current = id;
      thinkingIdRef.current = null; // Reset thinking state for new turn
      setMessages((prev) => [...prev, { id, role: "assistant", text: "", timestamp: Date.now() }]);
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
  }, [gw, resumeTarget, projectContextKey]);

  // Propagate only durable resume targets to parent tab state.
  useEffect(() => {
    // `sessionId` is the live tui_gateway session handle returned by
    // session.create/session.resume. It is not a durable SessionDB id and must
    // not be written into the tab URL as a resume target; doing so makes the
    // parent feed it back as `resumeTarget`, which tears down the live socket
    // and loops through session.create forever. Persist only the durable DB
    // target the user explicitly resumed.
    if (resumeTarget) onSessionCreatedRef.current?.(resumeTarget);
  }, [resumeTarget]);

  // Auto-submit queued message when the current turn finishes.
  useEffect(() => {
    if (!running && queuedRef.current) {
      const queued = queuedRef.current;
      queuedRef.current = null;
      setHasQueued(false);
      setTimeout(() => submitRef.current(queued.text, queued.attachments), 0);
    }
  }, [running]);

  // Auto-scroll is handled by ChatMessageList internally

  const submit = useCallback(
    async (text: string, explicitAttachments?: DraftAttachment[]) => {
      const trimmed = text.trim();
      const attachmentsToSend = explicitAttachments ?? draftAttachments;
      if (!trimmed && attachmentsToSend.length === 0) return;
      if (!sessionId) {
        setInput(trimmed);
        setError(
          "Chat is still connecting. Submit again once the status is open.",
        );
        return;
      }
      if (running) {
        setInput(trimmed);
        queuedRef.current = { text: trimmed, attachments: attachmentsToSend };
        if (!explicitAttachments) setDraftAttachments([]);
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
          { id: `slash-${Date.now()}`, role: "user", text: trimmed, timestamp: Date.now() },
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
            onSessionCreatedRef.current?.(resumed.resumed ?? arg);
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
                timestamp: Date.now(),
              },
            ]);
          }
        }

        if (name === "queue" && arg) {
          setInput("");
          queuedRef.current = { text: arg, attachments: [] };
          setHasQueued(true);
          setMessages((prev) => [
            ...prev,
            {
              id: `queued-${Date.now()}`,
              role: "status",
              text: `已排队消息，当前轮次完成后自动提交：${arg.slice(0, 80)}${arg.length > 80 ? "…" : ""}`,
              timestamp: Date.now(),
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
                { id: `system-${Date.now()}`, role: "status", text: body, timestamp: Date.now() },
              ]),
            send: (message) => submit(message),
          },
        });
        return;
      }

      setError(null);
      setUploading(attachmentsToSend.length > 0);
      let uploaded: ChatAttachmentUpload[] = [];
      try {
        uploaded = await uploadChatAttachments(attachmentsToSend.map((item) => item.file));
      } catch (e) {
        setUploading(false);
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      setUploading(false);
      if (!explicitAttachments) setDraftAttachments([]);
      attachmentsToSend.forEach((item) => {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
      const attachmentSummary = uploaded.length
        ? `\n\n[附件]\n${uploaded.map((item) => `- ${item.kind === "image" ? "图片" : "文件"} ${item.name} (${formatBytes(item.size)})`).join("\n")}`
        : "";
      setMessages((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: "user", text: `${trimmed || "请处理附件。"}${attachmentSummary}`, timestamp: Date.now() },
      ]);
      setInput("");
      setRunning(true);
      try {
        await gw.request("prompt.submit", {
          session_id: sessionId,
          text: trimmed || "请处理附件。",
          attachments: uploaded,
          project_context: projectContext,
          ...(reasoningEffort !== "auto" && { reasoning_effort: reasoningEffort }),
        });
      } catch (e) {
        setRunning(false);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [draftAttachments, gw, projectContext, running, sessionId, reasoningEffort],
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
          timestamp: Date.now(),
        },
      ]);
    } catch (e) {
      setStopping(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [gw, running, sessionId, stopping]);

  const queueCurrentInput = useCallback(() => {
    const trimmed = input.trim();
    if ((!trimmed && draftAttachments.length === 0) || !running) return;
    queuedRef.current = { text: trimmed, attachments: draftAttachments };
    setInput("");
    setDraftAttachments([]);
    setHasQueued(true);
    setError(null);
    setMessages((prev) => [
      ...prev,
      {
        id: `queued-${Date.now()}`,
        role: "status",
        text: `已排队消息，当前轮次完成后自动提交：${trimmed.slice(0, 80)}${trimmed.length > 80 ? "…" : ""}${draftAttachments.length ? `（含 ${draftAttachments.length} 个附件）` : ""}`,
        timestamp: Date.now(),
      },
    ]);
  }, [draftAttachments, input, running]);

  const addFiles = useCallback((files: FileList | File[]) => {
    const incoming = Array.from(files).slice(0, 10);
    if (!incoming.length) return;
    setDraftAttachments((prev) => {
      const next = [...prev];
      for (const file of incoming) {
        if (next.length >= 10) break;
        next.push({
          id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID?.() ?? Math.random()}`,
          file,
          previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
        });
      }
      return next;
    });
  }, []);

  const removeDraftAttachment = useCallback((id: string) => {
    setDraftAttachments((prev) => {
      const found = prev.find((item) => item.id === id);
      if (found?.previewUrl) URL.revokeObjectURL(found.previewUrl);
      return prev.filter((item) => item.id !== id);
    });
  }, []);

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
            Lex Gateway Web
          </div>
          <div className="flex items-center gap-3">
            <div className="truncate text-sm text-muted-foreground">
              JSON-RPC session · native tools, slash commands, and workflow
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

      {/* Chat message list — bubble style */}
      <ChatMessageList
        messages={messages}
        thinkingBlocks={thinkingBlocks}
        tools={tools}
        subagents={subagents}
        swarmState={swarmState}
        running={running}
        assistantIdRef={assistantIdRef}
        className="min-h-0 flex-1"
      />

      {/* Error banner */}
      {error && (
        <div className="border-t border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Queue indicator */}
      {hasQueued && (
        <div className="lex-panel-reveal lex-queue-ribbon flex items-center gap-2 border-t border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-xs">
          <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-amber-400" />
          <span className="flex-1 text-amber-300/80">
            Message queued — will submit when current turn completes.{" "}
            {queuedRef.current && (
              <span className="text-muted-foreground/60">
                ({queuedRef.current.text.slice(0, 50)}
                {queuedRef.current.text.length > 50 ? "…" : ""}
                {queuedRef.current.attachments.length ? ` · ${queuedRef.current.attachments.length} attachment(s)` : ""})
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
        onDragOver={(ev) => {
          if (ev.dataTransfer?.types.includes("Files")) {
            ev.preventDefault();
          }
        }}
        onDrop={(ev) => {
          if (ev.dataTransfer?.files?.length) {
            ev.preventDefault();
            addFiles(ev.dataTransfer.files);
          }
        }}
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit(input);
        }}
      >
        {draftAttachments.length > 0 && (
          <div className="lex-panel-reveal flex max-h-28 flex-wrap gap-2 overflow-y-auto rounded border border-current/10 bg-black/10 p-2">
            {draftAttachments.map((item) => {
              const isImage = item.file.type.startsWith("image/");
              return (
                <div
                  key={item.id}
                  className="group flex max-w-[16rem] items-center gap-2 rounded border border-current/15 bg-muted/10 px-2 py-1 text-xs"
                  title={`${item.file.name} · ${formatBytes(item.file.size)}`}
                >
                  {isImage && item.previewUrl ? (
                    <img
                      src={item.previewUrl}
                      alt=""
                      className="h-8 w-8 shrink-0 rounded object-cover"
                    />
                  ) : isImage ? (
                    <ImageIcon className="h-4 w-4 shrink-0 text-primary/80" />
                  ) : (
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0">
                    <div className="truncate text-foreground/90">{item.file.name}</div>
                    <div className="font-mono-ui text-[0.6rem] text-muted-foreground/70">
                      {formatBytes(item.file.size)}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeDraftAttachment(item.id)}
                    className="ml-1 rounded p-0.5 text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`Remove ${item.file.name}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(ev) => setInput(ev.target.value)}
            onPaste={(ev) => {
              const files = Array.from(ev.clipboardData.files || []);
              if (files.length) {
                addFiles(files);
              }
            }}
            onKeyDown={(ev) => {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                void submit(input);
              }
            }}
            disabled={!sessionId || conn !== "open"}
            rows={2}
            placeholder="输入任务，或使用 /resume、/swarm、/kanban 等命令。Shift+Enter 换行。Cmd+K 命令面板。"
            className="min-h-12 flex-1 resize-none rounded border border-current/15 bg-black/10 px-3 py-2 text-sm outline-none focus:border-primary/60"
          />
          <div className="flex shrink-0 items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(ev) => {
                if (ev.currentTarget.files) addFiles(ev.currentTarget.files);
                ev.currentTarget.value = "";
              }}
            />
            <Button
              type="button"
              ghost
              onClick={() => fileInputRef.current?.click()}
              disabled={!sessionId || conn !== "open" || uploading}
              title="Attach images or files"
              aria-label="Attach images or files"
              className="px-3"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            {running && (
              <Button
                type="button"
                ghost
                onClick={queueCurrentInput}
                disabled={!sessionId || (!input.trim() && draftAttachments.length === 0) || hasQueued}
                title={hasQueued ? "A message is already queued" : "Queue message after current turn"}
                aria-label="Queue message after current turn"
                className="px-3"
              >
                <ListPlus className="h-4 w-4" />
              </Button>
            )}
            <Button
              type={running ? "button" : "submit"}
              onClick={running ? interrupt : undefined}
              disabled={!sessionId || stopping || uploading || (!running && !input.trim() && draftAttachments.length === 0)}
              title={running ? "Stop current turn" : uploading ? "Uploading attachments" : "Send"}
              aria-label={running ? "Stop current turn" : uploading ? "Uploading attachments" : "Send"}
              className="px-3"
            >
              {uploading ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : running ? (
                <Square className="h-4 w-4" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
        {/* reasoning effort moved to ChatTopBar */}
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

// ExecutionInspector + SubagentCard removed — tools and subagents now render inline via ChatMessageList.
