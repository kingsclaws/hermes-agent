import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@/components/ui/card";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";
import { Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "status";
  text: string;
};

type ToolLine = {
  id: string;
  name: string;
  status: "running" | "done" | "error";
  summary?: string;
};

const WORKFLOW_PROMPT_EVENT = "lex-workflow-prompt";

export function dispatchWorkflowPrompt(prompt: string) {
  window.dispatchEvent(new CustomEvent(WORKFLOW_PROMPT_EVENT, { detail: prompt }));
}

export function NativeChatSurface() {
  const gw = useMemo(() => new GatewayClient(), []);
  const [conn, setConn] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tools, setTools] = useState<ToolLine[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const assistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const offState = gw.onState(setConn);
    const offStart = gw.on("message.start", () => {
      const id = `assistant-${Date.now()}`;
      assistantIdRef.current = id;
      setMessages((prev) => [...prev, { id, role: "assistant", text: "" }]);
      setRunning(true);
    });
    const offDelta = gw.on<{ text?: string; rendered?: string }>("message.delta", (ev) => {
      const delta = ev.payload?.rendered ?? ev.payload?.text ?? "";
      const id = assistantIdRef.current;
      if (!id || !delta) return;
      setMessages((prev) =>
        prev.map((msg) => (msg.id === id ? { ...msg, text: msg.text + delta } : msg)),
      );
    });
    const offComplete = gw.on("message.complete", () => {
      setRunning(false);
      assistantIdRef.current = null;
    });
    const offError = gw.on<{ message?: string }>("error", (ev) => {
      setError(ev.payload?.message ?? "Agent error");
      setRunning(false);
    });
    const offToolStart = gw.on<{ tool_id?: string; name?: string }>("tool.start", (ev) => {
      const id = ev.payload?.tool_id ?? `tool-${Date.now()}`;
      setTools((prev) =>
        [
          ...prev,
          { id, name: ev.payload?.name ?? "tool", status: "running" as const },
        ].slice(-30),
      );
    });
    const offToolComplete = gw.on<{ tool_id?: string; summary?: string; error?: string }>(
      "tool.complete",
      (ev) => {
        const id = ev.payload?.tool_id;
        if (!id) return;
        setTools((prev) =>
          prev.map((tool) =>
            tool.id === id
              ? {
                  ...tool,
                  status: ev.payload?.error ? "error" : "done",
                  summary: ev.payload?.error ?? ev.payload?.summary,
                }
              : tool,
          ),
        );
      },
    );

    gw.connect()
      .then(() => {
        if (cancelled) return null;
        return gw.request<{ session_id: string }>("session.create", {});
      })
      .then((created) => {
        if (!cancelled && created?.session_id) setSessionId(created.session_id);
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
      offToolStart();
      offToolComplete();
      gw.close();
    };
  }, [gw]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, tools]);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (!sessionId) {
        setInput(trimmed);
        setError("Chat is still connecting. Submit again once the status is open.");
        return;
      }
      if (running) {
        setInput(trimmed);
        setError("A turn is already running. Submit this after the current turn finishes.");
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
        await gw.request("prompt.submit", { session_id: sessionId, text: trimmed });
      } catch (e) {
        setRunning(false);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [gw, running, sessionId],
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
      <div className="flex items-center justify-between gap-2 border-b border-current/10 px-3 py-2">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            native legal chat
          </div>
          <div className="truncate text-sm text-muted-foreground">
            JSON-RPC session · tools and workflow rendered by Web UI
          </div>
        </div>
        <span
          className={cn(
            "rounded border px-2 py-0.5 text-[0.65rem]",
            conn === "open"
              ? "border-success/40 text-success"
              : "border-current/20 text-muted-foreground",
          )}
        >
          {conn}
        </span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.length === 0 && (
          <div className="rounded border border-dashed border-current/15 p-4 text-sm text-muted-foreground">
            这里是原生 Web Chat，不是 TUI。右侧 workflow 操作会直接提交到这个会话。
          </div>
        )}
        {messages.map((message) => (
          <div
            key={message.id}
            className={cn(
              "max-w-[92%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-6",
              message.role === "user"
                ? "ml-auto border-primary/30 bg-primary/10"
                : "mr-auto border-current/10 bg-black/10",
            )}
          >
            {message.text || (message.role === "assistant" ? "…" : "")}
          </div>
        ))}
        {tools.length > 0 && (
          <div className="space-y-1 rounded border border-current/10 bg-black/10 p-2">
            <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground">
              tool calls
            </div>
            {tools.map((tool) => (
              <div key={tool.id} className="flex items-start gap-2 text-xs">
                <span
                  className={cn(
                    "mt-1 h-2 w-2 shrink-0 rounded-full",
                    tool.status === "running"
                      ? "bg-warning"
                      : tool.status === "error"
                        ? "bg-destructive"
                        : "bg-success",
                  )}
                />
                <div className="min-w-0">
                  <span className="font-medium">{tool.name}</span>
                  {tool.summary && (
                    <span className="ml-2 text-muted-foreground">{tool.summary}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="border-t border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <form
        className="flex shrink-0 gap-2 border-t border-current/10 p-3"
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit(input);
        }}
      >
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
          placeholder="输入法律工作指令。Shift+Enter 换行。"
          className="min-h-12 flex-1 resize-none rounded border border-current/15 bg-black/10 px-3 py-2 text-sm outline-none focus:border-primary/60"
        />
        <Button
          type="submit"
          disabled={!sessionId || running || !input.trim()}
          className="self-end px-3"
        >
          {running ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
        </Button>
      </form>
    </Card>
  );
}
