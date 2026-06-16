import {
  useEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
} from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  Send,
  Users,
  Bot,
  Gavel,
  PenTool,
  Search,
  Layout,
  Link,
  FileText,
  Languages,
  Wifi,
  WifiOff,
  Play,
  Building2,
  ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import type { RoomMessage, BotInfo, ProjectInfo, WorkflowDefinition } from "@/lib/api";
import { ChatroomClient } from "@/lib/roomClient";
import type { ConnectionState } from "@/lib/roomClient";
import { cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Markdown } from "@/components/Markdown";
import { PluginSlot } from "@/plugins";

// ---------------------------------------------------------------------------
// Bot avatar icon mapping
// ---------------------------------------------------------------------------

const BOT_ICONS: Record<string, typeof Bot> = {
  "lex-coordinator": Gavel,
  "lex-drafter": PenTool,
  "lex-reviewer-content": Search,
  "lex-reviewer-format": Layout,
  "lex-reviewer-xref": Link,
  "lex-reviewer-ts": FileText,
  "lex-reviewer-translation": Languages,
};

function BotAvatar({ profile, size = 16 }: { profile: string; size?: number }) {
  const Icon = BOT_ICONS[profile] ?? Bot;
  return <Icon style={{ width: size, height: size }} className="shrink-0" />;
}

// ---------------------------------------------------------------------------
// Connection badge
// ---------------------------------------------------------------------------

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const connected = state === "open";
  return (
    <Badge tone={connected ? "success" : "secondary"} className="text-[10px]">
      {connected ? (
        <span className="flex items-center gap-1">
          <Wifi className="w-3 h-3" /> Live
        </span>
      ) : (
        <span className="flex items-center gap-1">
          <WifiOff className="w-3 h-3" /> {state === "connecting" ? "Connecting..." : "Offline"}
        </span>
      )}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Bot sidebar
// ---------------------------------------------------------------------------

function BotSidebar({
  bots,
  connectionState,
  onMention,
  onRunWorkflow,
  compiling,
  workflowDefs,
  projectName,
}: {
  bots: BotInfo[];
  connectionState: ConnectionState;
  onMention: (profile: string) => void;
  onRunWorkflow?: (workflowId: string) => void;
  compiling?: boolean;
  workflowDefs?: WorkflowDefinition[];
  projectName?: string;
}) {
  const statusTone = (status: string): "success" | "warning" | "secondary" | "destructive" => {
    if (status === "done") return "success";
    if (status === "running") return "warning";
    if (status === "blocked") return "destructive";
    return "secondary";
  };

  return (
    <div className="w-56 shrink-0 border-r border-border flex flex-col bg-card/30">
      <div className="p-3 border-b border-border">
        <div className="flex items-center gap-2 mb-1">
          <Users className="w-4 h-4 text-secondary" />
          <span className="text-xs font-semibold uppercase tracking-wide text-secondary">
            Bots
          </span>
        </div>
        <ConnectionBadge state={connectionState} />
      </div>

      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1">
        {bots.length === 0 ? (
          <p className="text-[10px] text-secondary text-center py-4">
            No bots. Trigger a workflow to populate bots.
          </p>
        ) : (
          bots.map((bot) => (
            <button
              key={bot.id}
              type="button"
              className={cn(
                "flex items-center gap-2 px-2 py-1.5 rounded-md text-left hover:bg-secondary/10 transition-colors w-full",
              )}
              onClick={() => onMention(bot.id)}
              title={`@${bot.id} — ${bot.description || ""}`}
            >
              <BotAvatar profile={bot.id} size={14} />
              <div className="min-w-0 flex-1">
                <span className="text-[11px] font-medium truncate block">
                  {bot.name || bot.id}
                </span>
                {bot.kind && (
                  <span className="text-[9px] text-secondary">{bot.kind}</span>
                )}
              </div>
              {bot.status && (
                <Badge tone={statusTone(bot.status)} className="text-[9px] shrink-0">
                  {bot.status}
                </Badge>
              )}
            </button>
          ))
        )}
      </div>

      {onRunWorkflow && (
        <div className="p-2 border-t border-border flex flex-col gap-1">
          {projectName && (
            <div className="flex items-center gap-1.5 px-1 mb-1">
              <Building2 className="w-3 h-3 text-secondary shrink-0" />
              <span className="text-[10px] font-medium text-secondary truncate">{projectName}</span>
            </div>
          )}
          {workflowDefs && workflowDefs.length > 0 ? (
            workflowDefs.map((wf) => (
              <Button
                key={wf.id}
                size="xs"
                ghost
                className="w-full text-xs justify-start"
                onClick={() => onRunWorkflow(wf.id)}
                disabled={compiling}
              >
                {compiling ? (
                  <Spinner className="w-3 h-3" />
                ) : (
                  <Play className="w-3 h-3" />
                )}
                <span className="ml-1 truncate">{wf.id.replace(/_/g, " ")}</span>
                <span className="text-[9px] text-secondary ml-auto shrink-0">{wf.node_count}n</span>
              </Button>
            ))
          ) : (
            <Button
              size="xs"
              ghost
              className="w-full text-xs justify-start"
              onClick={() => onRunWorkflow("")}
              disabled={compiling}
            >
              {compiling ? (
                <Spinner className="w-3 h-3" />
              ) : (
                <Play className="w-3 h-3" />
              )}
              <span className="ml-1">Run Workflow</span>
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message list
// ---------------------------------------------------------------------------

function MessageList({ messages }: { messages: RoomMessage[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center max-w-xs">
          <Users className="w-10 h-10 text-secondary/30 mx-auto mb-3" />
          <p className="text-sm text-secondary font-medium mb-1">
            No messages yet
          </p>
          <p className="text-xs text-secondary">
            Type a message and @mention a bot to get started, or click <strong>Run Workflow</strong> in the sidebar.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-2">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single message bubble
// ---------------------------------------------------------------------------

function MessageBubble({ message }: { message: RoomMessage }) {
  switch (message.kind) {
    case "user":
      return (
        <div className="flex justify-end">
          <div className="max-w-[75%] px-3 py-2 rounded-lg bg-primary/10 border border-primary/20 text-sm">
            {message.text}
          </div>
        </div>
      );

    case "bot":
      return (
        <div className="flex gap-2">
          <div className="mt-1 shrink-0">
            <div className="w-6 h-6 rounded-full bg-secondary/10 flex items-center justify-center">
              <BotAvatar profile={message.senderProfile || ""} size={14} />
            </div>
          </div>
          <div className="max-w-[75%] min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              <span className="text-[11px] font-semibold">
                {message.sender || "Bot"}
              </span>
              {message.taskId && (
                <span className="text-[9px] text-secondary font-mono-ui">
                  {message.taskId.slice(0, 12)}
                </span>
              )}
            </div>
            <div className="px-3 py-2 rounded-lg bg-secondary/5 border border-border text-sm">
              <Markdown content={message.text} />
            </div>
          </div>
        </div>
      );

    case "status":
      return (
        <div className="flex justify-center">
          <div className="max-w-[85%] px-3 py-1 rounded-full bg-secondary/5 border border-border text-[11px] text-secondary text-center">
            {message.text}
          </div>
        </div>
      );

    case "system":
      return (
        <div className="flex justify-center">
          <div className="max-w-[85%] px-3 py-1 text-[11px] text-secondary/60 italic text-center">
            {message.text}
          </div>
        </div>
      );

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Composer with @mention support
// ---------------------------------------------------------------------------

function RoomComposer({
  value,
  onChange,
  onSubmit,
  bots,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  bots: BotInfo[];
  disabled?: boolean;
}) {
  const [showMentions, setShowMentions] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const v = e.target.value;
      onChange(v);

      // Detect @mention trigger
      const cursorPos = e.target.selectionStart ?? v.length;
      const textBefore = v.slice(0, cursorPos);
      const atMatch = textBefore.match(/@([\w-]*)$/);
      if (atMatch) {
        setMentionFilter(atMatch[1].toLowerCase());
        setShowMentions(true);
      } else {
        setShowMentions(false);
      }
    },
    [onChange],
  );

  const selectMention = useCallback(
    (profile: string) => {
      const cursorPos = textareaRef.current?.selectionStart ?? value.length;
      const textBefore = value.slice(0, cursorPos);
      const textAfter = value.slice(cursorPos);
      const atIdx = textBefore.lastIndexOf("@");
      const newText = textBefore.slice(0, atIdx) + `@${profile} ` + textAfter;
      onChange(newText);
      setShowMentions(false);
      textareaRef.current?.focus();
    },
    [value, onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (showMentions) {
          const filtered = bots.filter(
            (b) =>
              b.id.toLowerCase().includes(mentionFilter) ||
              b.name.toLowerCase().includes(mentionFilter),
          );
          if (filtered.length > 0) {
            selectMention(filtered[0].id);
            return;
          }
        }
        onSubmit();
      }
      if (e.key === "Escape") {
        setShowMentions(false);
      }
    },
    [onSubmit, showMentions, mentionFilter, bots, selectMention],
  );

  const filteredBots = useMemo(() => {
    if (!mentionFilter) return bots;
    const f = mentionFilter.toLowerCase();
    return bots.filter(
      (b) => b.id.toLowerCase().includes(f) || b.name.toLowerCase().includes(f),
    );
  }, [bots, mentionFilter]);

  return (
    <div className="relative">
      {showMentions && filteredBots.length > 0 && (
        <div className="absolute bottom-full left-0 mb-1 w-64 max-h-40 overflow-y-auto bg-popover border border-border rounded-md shadow-lg z-50">
          {filteredBots.map((bot) => (
            <button
              key={bot.id}
              type="button"
              className="flex items-center gap-2 px-3 py-1.5 w-full text-left hover:bg-secondary/10 text-xs"
              onClick={() => selectMention(bot.id)}
            >
              <BotAvatar profile={bot.id} size={12} />
              <span className="font-medium">{bot.name || bot.id}</span>
              <span className="text-secondary ml-auto">{bot.kind || bot.id}</span>
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={disabled ? "Connecting..." : "Type a message... Use @ to mention a bot"}
        disabled={disabled}
        rows={2}
        className="w-full bg-input-bg border border-border rounded-md px-3 py-2 text-sm resize-none
                   placeholder:text-secondary/60 focus:outline-none focus:border-primary/40
                   disabled:opacity-50"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function LegalSwarmChat() {
  const [searchParams, setSearchParams] = useSearchParams();
  const board = searchParams.get("board") ?? "";
  const runId = searchParams.get("run") ?? "";
  const projectId = searchParams.get("project") ?? "";

  const isWorkflowRoom = !!(board && runId);
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [bots, setBots] = useState<BotInfo[]>([]);
  const [input, setInput] = useState("");
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [statusLoading, setStatusLoading] = useState(true);
  const [compiling, setCompiling] = useState(false);
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [projectSelectorOpen, setProjectSelectorOpen] = useState(false);
  const [workflowDefs, setWorkflowDefs] = useState<WorkflowDefinition[]>([]);
  const { showToast } = useToast();
  const navigate = useNavigate();

  const clientRef = useRef<ChatroomClient | null>(null);
  const msgIdSet = useRef<Set<string>>(new Set());
  const selectorRef = useRef<HTMLDivElement>(null);

  // ── Connect room / chatroom ────────────────────────────────────────────

  useEffect(() => {
    const client = new ChatroomClient();
    clientRef.current = client;

    client.onState((s) => setConnectionState(s));

    client.onMessage((msg) => {
      setMessages((prev) => {
        if (msgIdSet.current.has(msg.id)) return prev;
        msgIdSet.current.add(msg.id);
        return [...prev, msg];
      });
    });

    if (isWorkflowRoom) {
      client.connect(board, runId).catch((e) => {
        showToast(e?.message ?? "Failed to connect to room", "error");
      });
    } else {
      // Auto-join the default chatroom
      client.connectChatroom("main").catch((e) => {
        showToast(e?.message ?? "Failed to connect to chatroom", "error");
      });
    }

    return () => {
      client.close();
      clientRef.current = null;
      msgIdSet.current.clear();
    };
  }, [board, runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load project context ───────────────────────────────────────────────

  useEffect(() => {
    if (projectId) {
      api.getProject(projectId).then(setProject).catch(() => setProject(null));
      api.fetchWorkflowDefinitions().then((res) => {
        setWorkflowDefs(res.definitions ?? []);
      }).catch(() => {});
    } else {
      setProject(null);
      setWorkflowDefs([]);
      // Load all projects for the selector dropdown
      api.fetchProjects().then((res) => {
        setProjects(res.projects ?? []);
      }).catch(() => {});
    }
  }, [projectId]);

  // Close project selector on outside click
  useEffect(() => {
    if (!projectSelectorOpen) return;
    const handler = (e: MouseEvent) => {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node)) {
        setProjectSelectorOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [projectSelectorOpen]);

  // ── Load bots ──────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        if (isWorkflowRoom) {
          const status = await api.getRoomStatus(board, runId);
          if (cancelled) return;
          setBots(status.bots);
        } else {
          const res = await api.fetchChatroomBots();
          if (cancelled) return;
          setBots((res.bots ?? []).map((b) => ({
            id: b.id,
            name: b.name,
            kind: b.kind,
            icon: b.icon,
            profile: b.profile,
            description: b.kind || "",
          } as BotInfo)));
        }
      } catch { /* ignore */ }
      finally { if (!cancelled) setStatusLoading(false); }
    }
    load();

    const timer = setInterval(async () => {
      try {
        if (isWorkflowRoom) {
          const status = await api.getRoomStatus(board, runId);
          setBots(status.bots);
        } else {
          const res = await api.fetchChatroomBots();
          setBots((res.bots ?? []).map((b) => ({
            id: b.id,
            name: b.name,
            kind: b.kind,
            icon: b.icon,
            profile: b.profile,
            description: b.kind || "",
          } as BotInfo)));
        }
      } catch { /* ignore */ }
    }, 30000);

    return () => { cancelled = true; clearInterval(timer); };
  }, [board, runId, isWorkflowRoom]);

  // ── Load historical messages (workflow rooms only) ────────────────────

  useEffect(() => {
    if (!isWorkflowRoom) return;
    api.getRoomMessages(board, runId).then((res) => {
      setMessages((prev) => {
        if (prev.length > 0) return prev;
        const msgs = res.messages ?? [];
        for (const m of msgs) msgIdSet.current.add(m.id);
        return msgs;
      });
    }).catch(() => {});
  }, [board, runId, isWorkflowRoom]);

  // ── Handlers ──────────────────────────────────────────────────────────

  const handleSubmit = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    clientRef.current?.sendMessage(text);
    // Optimistically add user message
    const userMsg: RoomMessage = {
      id: `user-${Date.now()}`,
      kind: "user",
      text,
      timestamp: Date.now() / 1000,
    };
    msgIdSet.current.add(userMsg.id);
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
  }, [input]);

  const handleMention = useCallback((profile: string) => {
    setInput((prev) => prev + `@${profile} `);
  }, []);

  const handleRunWorkflow = useCallback(async (workflowId: string) => {
    if (!projectId || compiling) return;
    setCompiling(true);
    try {
      const result = await api.compileSwarmWorkflow(projectId, workflowId);
      const params = new URLSearchParams();
      params.set("board", result.board);
      params.set("run", result.run_id);
      params.set("project", projectId);
      navigate(`/swarm-chat?${params.toString()}`);
    } catch (e: any) {
      showToast(e?.message ?? "Failed to compile workflow", "error");
    } finally {
      setCompiling(false);
    }
  }, [projectId, compiling, navigate, showToast]);

  // ── Loading state ────────────────────────────────────────────────────

  if (statusLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Spinner className="text-lg text-primary" />
      </div>
    );
  }

  // ── Project selection ─────────────────────────────────────────────────

  const selectProject = useCallback((id: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("project", id);
    setSearchParams(params);
    setProjectSelectorOpen(false);
  }, [searchParams, setSearchParams]);

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <PluginSlot name="swarmchat:top" />

      {/* Project context bar */}
      {!projectId && !isWorkflowRoom && (
        <div className="px-4 py-2 border-b border-border bg-card/30 flex items-center gap-3">
          <Building2 className="w-4 h-4 text-secondary shrink-0" />
          <span className="text-xs text-secondary">No project selected. </span>
          <div className="relative" ref={selectorRef}>
            <Button
              size="xs"
              ghost
              className="text-xs"
              onClick={() => setProjectSelectorOpen((v) => !v)}
            >
              <span>Select a project</span>
              <ChevronDown className="w-3 h-3 ml-1" />
            </Button>
            {projectSelectorOpen && (
              <div className="absolute top-full left-0 mt-1 w-64 max-h-48 overflow-y-auto bg-popover border border-border rounded-md shadow-lg z-50">
                {projects.length === 0 ? (
                  <p className="text-xs text-secondary p-2">No projects available</p>
                ) : (
                  projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className="flex items-center gap-2 px-3 py-1.5 w-full text-left hover:bg-secondary/10 text-xs"
                      onClick={() => selectProject(p.id)}
                    >
                      <Building2 className="w-3 h-3 text-secondary shrink-0" />
                      <span className="font-medium truncate">{p.name || p.id}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          <span className="text-[10px] text-secondary ml-auto">
            Select a project to enable workflow compilation
          </span>
        </div>
      )}

      {/* Project header badge (when project is selected) */}
      {projectId && project && !isWorkflowRoom && (
        <div className="px-4 py-1.5 border-b border-border bg-primary/5 flex items-center gap-2">
          <Building2 className="w-3.5 h-3.5 text-primary shrink-0" />
          <span className="text-xs font-medium">{project.name || projectId}</span>
          {project.client && (
            <span className="text-[10px] text-secondary">{project.client}</span>
          )}
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <BotSidebar
          bots={bots}
          connectionState={connectionState}
          onMention={handleMention}
          onRunWorkflow={projectId ? handleRunWorkflow : undefined}
          compiling={compiling}
          workflowDefs={projectId && !isWorkflowRoom ? workflowDefs : undefined}
          projectName={project?.name || projectId}
        />

        <div className="flex flex-1 flex-col min-w-0">
          <MessageList messages={messages} />

          <div className="px-4 py-3 border-t border-border flex items-end gap-2">
            <div className="flex-1">
              <RoomComposer
                value={input}
                onChange={setInput}
                onSubmit={handleSubmit}
                bots={bots}
                disabled={connectionState !== "open"}
              />
            </div>
            <Button
              size="sm"
              onClick={handleSubmit}
              disabled={connectionState !== "open" || !input.trim()}
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>

      <PluginSlot name="swarmchat:bottom" />
    </div>
  );
}
