import { Bot, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/Markdown";

export interface ChatBubbleProps {
  id: string;
  role: "user" | "assistant" | "status";
  text: string;
  timestamp: number;
  senderName?: string;
  isStreaming?: boolean;
  className?: string;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ChatBubble({
  role,
  text,
  timestamp,
  senderName,
  isStreaming,
  className,
}: ChatBubbleProps) {
  if (role === "status") {
    return (
      <div className={cn("flex justify-center", className)}>
        <div className="rounded border border-current/10 bg-muted/10 px-3 py-1.5 text-xs text-muted-foreground max-w-[85%] text-center">
          {text}
        </div>
      </div>
    );
  }

  const isUser = role === "user";

  return (
    <div className={cn("flex gap-2.5", isUser ? "flex-row-reverse" : "flex-row", className)}>
      {/* Avatar */}
      <div
        className={cn(
          "h-7 w-7 rounded-full flex items-center justify-center shrink-0 mt-0.5",
          isUser
            ? "bg-primary/15 text-primary"
            : "bg-muted/15 text-muted-foreground",
        )}
      >
        {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </div>

      {/* Bubble body */}
      <div className={cn("min-w-0 max-w-[82%] flex flex-col", isUser ? "items-end" : "items-start")}>
        {/* Sender name + time */}
        <div
          className={cn(
            "flex items-center gap-2 mb-0.5 px-0.5",
            isUser && "flex-row-reverse",
          )}
        >
          <span className="text-[0.65rem] font-medium text-muted-foreground/80">
            {isUser ? "You" : senderName || "Hermes"}
          </span>
          <span className="text-[0.6rem] text-muted-foreground/50 tabular-nums">
            {formatTime(timestamp)}
          </span>
        </div>

        {/* Message content */}
        <div
          className={cn(
            "rounded-lg border px-3 py-2 text-sm leading-6",
            isUser
              ? "border-primary/30 bg-primary/10 rounded-tr-sm"
              : "border-current/10 bg-black/10 rounded-tl-sm",
          )}
        >
          {text ? (
            <Markdown content={text} streaming={isStreaming} />
          ) : (
            <span className="text-muted-foreground/50 animate-pulse">...</span>
          )}
        </div>
      </div>
    </div>
  );
}
