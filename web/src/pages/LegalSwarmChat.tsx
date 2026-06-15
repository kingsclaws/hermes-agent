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
  ChevronRight,
  MessagesSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import type { RoomMessage, BotInfo, ProjectInfo, WorkflowDefinition } from "@/lib/api";
import { RoomClient } from "@/lib/roomClient";
import type { ConnectionState } from "@/lib/roomClient";
import { cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Markdown } from "@/components/Markdown";
import { PluginSlot } from "@/plugins";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";

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
}: {
  bots: BotInfo[];
  connectionState: ConnectionState;
  onMention: (profile: string) => void;
  onRunWorkflow?: () => void;
  compiling?: boolean;
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
        <div className="p-2 border-t border-border">
          <Button
            size="xs"
            ghost
            className="w-full text-xs justify-start"
            onClick={onRunWorkflow}
            disabled={compiling}
          >
            {compiling ? (
              <Spinner className="w-3 h-3" />
            ) : (
              <Play className="w-3 h-3" />
            )}
            <span className="ml-1">Run Workflow</span>
          </Button>
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
  const [searchParams] = useSearchParams();
  const board = searchParams.get("board") ?? "";
  const runId = searchParams.get("run") ?? "";

  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [bots, setBots] = useState<BotInfo[]>([]);
  const [input, setInput] = useState("");
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [statusLoading, setStatusLoading] = useState(true);
  const [compiling] = useState(false);
  const { showToast } = useToast();
  const navigate = useNavigate();

  // ── Launcher state (when no board/run in URL) ────────────────────────

  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [workflowDefs, setWorkflowDefs] = useState<WorkflowDefinition[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (board && runId) return;
    api.fetchProjects().then((d) => {
      setProjects(d.projects ?? []);
      if ((d.projects ?? []).length > 0) setSelectedProjectId(d.projects[0].id);
    }).catch(() => {});
    api.fetchWorkflowDefinitions().then((d) => {
      setWorkflowDefs(d.definitions ?? []);
      if ((d.definitions ?? []).length > 0) setSelectedWorkflowId(d.definitions[0].id);
    }).catch(() => {});
  }, [board, runId]);

  const handleLaunch = useCallback(async () => {
    if (!selectedProjectId || !selectedWorkflowId || launching) return;
    setLaunching(true);
    try {
      const result = await api.createRoom(selectedProjectId, selectedWorkflowId);
      navigate(
        `/swarm-chat?board=${encodeURIComponent(result.board)}&run=${encodeURIComponent(result.run_id)}`,
      );
    } catch (e: any) {
      showToast(e?.message ?? "Failed to create chat room", "error");
      setLaunching(false);
    }
  }, [selectedProjectId, selectedWorkflowId, launching, navigate, showToast]);

  const clientRef = useRef<RoomClient | null>(null);
  const msgIdSet = useRef<Set<string>>(new Set());

  // ── Connect room client ───────────────────────────────────────────────

  useEffect(() => {
    if (!board || !runId) return;

    const client = new RoomClient();
    clientRef.current = client;

    client.onState((s) => setConnectionState(s));

    client.onMessage((msg) => {
      setMessages((prev) => {
        // Deduplicate by message id
        if (msgIdSet.current.has(msg.id)) return prev;
        msgIdSet.current.add(msg.id);
        return [...prev, msg];
      });
    });

    client.connect(board, runId).catch((e) => {
      showToast(e?.message ?? "Failed to connect to room", "error");
    });

    return () => {
      client.close();
      clientRef.current = null;
      msgIdSet.current.clear();
    };
  }, [board, runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load bots + room status ───────────────────────────────────────────

  useEffect(() => {
    if (!board || !runId) return;
    let cancelled = false;

    async function load() {
      try {
        const status = await api.getRoomStatus(board, runId);
        if (cancelled) return;
        setBots(status.bots);
      } catch {
        // Room or run may not exist yet
      } finally {
        if (!cancelled) setStatusLoading(false);
      }
    }
    load();

    // Auto-refresh bot status every 15s
    const timer = setInterval(async () => {
      try {
        const status = await api.getRoomStatus(board, runId);
        setBots(status.bots);
      } catch { /* ignore */ }
    }, 15000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [board, runId]);

  // ── Load historical messages on first load ────────────────────────────

  useEffect(() => {
    if (!board || !runId) return;
    api.getRoomMessages(board, runId).then((res) => {
      setMessages((prev) => {
        if (prev.length > 0) return prev; // already have WS messages
        const msgs = res.messages ?? [];
        for (const m of msgs) msgIdSet.current.add(m.id);
        return msgs;
      });
    }).catch(() => { /* ignore */ });
  }, [board, runId]);

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

  const handleRunWorkflow = useCallback(async () => {
    showToast(
      "Use LaunchPad to create a room for a workflow, or send a message with @mentions.",
      "success",
    );
  }, [showToast]);

  // ── Launcher (no board/run) ──────────────────────────────────────────

  if (!board || !runId) {
    const selectedWf = workflowDefs.find((w) => w.id === selectedWorkflowId);
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-full max-w-lg mx-auto px-4">
          <div className="text-center mb-6">
            <MessagesSquare className="w-10 h-10 text-primary/50 mx-auto mb-3" />
            <h2 className="text-lg font-semibold mb-1">Legal Swarm Chat</h2>
            <p className="text-sm text-secondary">
              All legal agent bots — coordinator, drafter, reviewers — in one real-time conversation.
            </p>
          </div>

          <Card>
            <CardContent className="p-4 flex flex-col gap-4">
              <div>
                <label className="text-xs font-semibold text-secondary uppercase tracking-wide block mb-1.5">
                  Project
                </label>
                <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={cn(
                        "flex items-center gap-2 px-3 py-2 rounded-md text-left text-sm transition-colors w-full",
                        p.id === selectedProjectId
                          ? "bg-primary/10 border border-primary/30"
                          : "hover:bg-secondary/5 border border-transparent",
                      )}
                      onClick={() => setSelectedProjectId(p.id)}
                    >
                      <Building2 className="w-3.5 h-3.5 text-secondary shrink-0" />
                      <span className="truncate">{p.name || p.id}</span>
                      {p.id === selectedProjectId && (
                        <ChevronRight className="w-3.5 h-3.5 text-primary shrink-0 ml-auto" />
                      )}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-secondary uppercase tracking-wide block mb-1.5">
                  Workflow
                </label>
                <div className="flex flex-col gap-1">
                  {workflowDefs.map((wf) => (
                    <button
                      key={wf.id}
                      type="button"
                      className={cn(
                        "flex flex-col px-3 py-2 rounded-md text-left transition-colors w-full",
                        wf.id === selectedWorkflowId
                          ? "bg-primary/10 border border-primary/30"
                          : "hover:bg-secondary/5 border border-transparent",
                      )}
                      onClick={() => setSelectedWorkflowId(wf.id)}
                    >
                      <span className="text-sm font-medium">{wf.id}</span>
                      <span className="text-[11px] text-secondary">{wf.pipeline}</span>
                    </button>
                  ))}
                </div>
              </div>

              <Button
                onClick={handleLaunch}
                disabled={!selectedProjectId || !selectedWorkflowId || launching}
                className="w-full"
                prefix={launching ? <Spinner className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              >
                {launching ? "Creating..." : "Start Chat Room"}
              </Button>
              {selectedWf && (
                <p className="text-[10px] text-secondary text-center -mt-2">
                  {selectedWf.node_count} bots &middot; {selectedWf.timeout_minutes}min timeout
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // ── Loading state ────────────────────────────────────────────────────

  if (statusLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Spinner className="text-lg text-primary" />
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <PluginSlot name="swarmchat:top" />

      <div className="flex flex-1 min-h-0">
        <BotSidebar
          bots={bots}
          connectionState={connectionState}
          onMention={handleMention}
          onRunWorkflow={handleRunWorkflow}
          compiling={compiling}
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
