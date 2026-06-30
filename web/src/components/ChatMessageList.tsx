import { useEffect, useLayoutEffect, useRef } from "react";
import { LoaderCircle, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import { ChatBubble } from "@/components/ChatBubble";
import { ThinkingStream, type ThinkingBlockData } from "@/components/ThinkingBlock";
import { SwarmInlineView } from "@/components/SwarmInlineView";
import type { ToolEntry } from "@/components/ToolCall";
import type { ChatMessage, SubagentLine } from "@/components/NativeChatSurface";

export interface ChatMessageListProps {
  messages: ChatMessage[];
  thinkingBlocks: ThinkingBlockData[];
  tools: ToolEntry[];
  subagents: SubagentLine[];
  swarmState: "idle" | "active" | "completed";
  running: boolean;
  assistantIdRef: React.MutableRefObject<string | null>;
  className?: string;
  kanbanBatchSummary?: { total: number; done: number; failed: number } | null;
}

export function ChatMessageList({
  messages,
  thinkingBlocks,
  tools,
  subagents,
  swarmState,
  running,
  assistantIdRef,
  className,
  kanbanBatchSummary,
}: ChatMessageListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const previousMessageCountRef = useRef(0);

  const scrollToBottom = (behavior: ScrollBehavior = "auto") => {
    const el = scrollRef.current;
    if (!el) return;
    bottomRef.current?.scrollIntoView({ block: "end", behavior });
    for (const target of scrollTargets(el)) {
      target.scrollTo({ top: target.scrollHeight, behavior });
    }
  };

  const scrollTargets = (start: HTMLElement): HTMLElement[] => {
    const targets: HTMLElement[] = [start];
    let node = start.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      const style = window.getComputedStyle(node);
      if (
        /(auto|scroll)/.test(style.overflowY) &&
        node.scrollHeight > node.clientHeight + 8
      ) {
        targets.push(node);
      }
      node = node.parentElement;
    }
    return targets;
  };

  // Restored sessions can add hundreds of messages at once. Scroll before
  // paint, then repeat across two frames after layout/fonts/iframes settle.
  useLayoutEffect(() => {
    if (messages.length === 0) return;
    const previousCount = previousMessageCountRef.current;
    const restoredHistory = messages.length > previousCount + 1;
    previousMessageCountRef.current = messages.length;
    const behavior: ScrollBehavior = restoredHistory ? "auto" : "smooth";
    scrollToBottom(restoredHistory ? "auto" : behavior);
    const raf1 = requestAnimationFrame(() => {
      scrollToBottom(behavior);
      requestAnimationFrame(() => scrollToBottom(behavior));
    });
    const timeout = window.setTimeout(() => scrollToBottom("auto"), 150);
    return () => {
      cancelAnimationFrame(raf1);
      window.clearTimeout(timeout);
    };
  }, [messages.length]);

  useEffect(() => {
    if (messages.length === 0) return;
    scrollToBottom(running ? "smooth" : "auto");
  }, [messages, tools, thinkingBlocks, running]);

  if (messages.length === 0) {
    return (
      <div className={cn("flex items-center justify-center", className)}>
        <div className="lex-message-enter rounded border border-dashed border-current/15 p-4 text-sm text-muted-foreground text-center max-w-md">
          Legal workspace ready. Submit a message or use Cmd+K for commands.
        </div>
      </div>
    );
  }

  const lastUserIdx = messages.reduce(
    (acc, m, i) => (m.role === "user" ? i : acc),
    -1,
  );
  const lastAssistantMsg = messages.filter((m) => m.role === "assistant").slice(-1)[0];

  return (
    <div ref={scrollRef} className={cn("min-h-0 space-y-3 overflow-y-auto px-3 py-3", className)}>
      {messages.map((message, idx) => (
        <div key={message.id}>
          {/* Thinking blocks — shown above current assistant message */}
          {message.role === "assistant" &&
            thinkingBlocks.length > 0 &&
            message.id === lastAssistantMsg?.id && (
              <ThinkingStream blocks={thinkingBlocks} className="mb-2" />
            )}

          <ChatBubble
            id={message.id}
            role={message.role}
            text={message.text}
            timestamp={message.timestamp}
            senderName={message.senderName}
            isStreaming={
              running && message.id === assistantIdRef.current
            }
          />

          {/* Tool calls — inline collapsed cards after assistant */}
          {message.role === "assistant" &&
            message.id === lastAssistantMsg?.id &&
            tools.length > 0 && (
              <div className="mt-1 space-y-1 pl-10">
                {tools
                  .slice()
                  .reverse()
                  .map((tool) => (
                    <InlineToolCall key={tool.id} tool={tool} />
                  ))}
              </div>
            )}

          {/* Swarm inline visualization */}
          {idx === lastUserIdx && (
            <SwarmInlineView
              subagents={subagents}
              swarmState={swarmState}
            />
          )}
        </div>
      ))}

      {/* Kanban batch completion card */}
      {kanbanBatchSummary && kanbanBatchSummary.total > 0 && (
        <div className="mt-2 pl-10">
          <div
            className={cn(
              "lex-tool-enter rounded border px-3 py-2 text-xs",
              kanbanBatchSummary.failed > 0
                ? "border-destructive/30 bg-destructive/[0.04]"
                : "border-green-500/30 bg-green-500/[0.04]",
            )}
          >
            <span className="font-medium">Kanban Tasks: </span>
            <span>
              {kanbanBatchSummary.done}/{kanbanBatchSummary.total} done
            </span>
            {kanbanBatchSummary.failed > 0 && (
              <span className="text-destructive ml-2">
                {kanbanBatchSummary.failed} failed
              </span>
            )}
          </div>
        </div>
      )}
      <div ref={bottomRef} aria-hidden className="h-px" />
    </div>
  );
}

function InlineToolCall({ tool }: { tool: ToolEntry }) {
  return (
    <div
      className={cn(
        "lex-tool-enter rounded border px-2.5 py-1.5 text-xs",
        tool.status === "running"
          ? "lex-running-surface border-primary/30 bg-primary/[0.04]"
          : tool.status === "error"
            ? "border-destructive/40 bg-destructive/[0.04]"
            : "border-current/10 bg-muted/10",
      )}
    >
      <div className="flex items-center gap-2">
        {tool.status === "running" ? (
          <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-primary" />
        ) : (
          <Wrench className="h-3 w-3 shrink-0 text-muted-foreground" />
        )}
        <span className="font-medium truncate">{tool.name}</span>
        <span
          className={cn(
            "shrink-0 text-[0.6rem] uppercase",
            tool.status === "running"
              ? "text-primary"
              : tool.status === "error"
                ? "text-destructive"
                : "text-muted-foreground",
          )}
        >
          {tool.status}
        </span>
      </div>
      {tool.context && (
        <div className="mt-1 text-muted-foreground/70 truncate max-w-prose">
          {tool.context}
        </div>
      )}
      {(tool.summary || tool.error) && (
        <div className="lex-panel-reveal mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap rounded bg-black/10 p-1.5 font-mono text-[0.65rem] text-muted-foreground">
          {tool.error ?? tool.summary}
        </div>
      )}
      {tool.inline_diff && (
        <div className="lex-panel-reveal mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap rounded bg-black/10 p-1.5 font-mono text-[0.65rem] text-muted-foreground">
          {tool.inline_diff}
        </div>
      )}
    </div>
  );
}
