import { BookOpen, MessageSquare, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";

export interface WelcomePanelProps {
  className?: string;
}

export function WelcomePanel({ className }: WelcomePanelProps) {
  const navigate = useNavigate();

  return (
    <div className={cn("flex items-center justify-center min-h-0 flex-1", className)}>
      <div className="flex flex-col items-center gap-4 max-w-md text-center px-4">
        {/* Greeting */}
        <div className="rounded-full bg-muted/30 p-4">
          <BookOpen className="h-8 w-8 text-muted-foreground/60" />
        </div>
        <h2 className="text-lg font-medium tracking-wide text-midground">
          Legal Workspace
        </h2>
        <p className="text-sm text-muted-foreground leading-relaxed max-w-xs">
          Select a project or session from the left panel to begin, or start a new
          conversation below.
        </p>

        {/* Quick actions */}
        <div className="flex flex-col gap-2 w-64">
          <button
            type="button"
            onClick={() => navigate("/chat")}
            className="inline-flex items-center gap-2 rounded border border-current/20 px-3 py-2 text-xs text-midground hover:bg-muted/10 transition-colors"
          >
            <Play className="h-3.5 w-3.5" />
            New Chat
          </button>
          <button
            type="button"
            onClick={() => navigate("/sessions")}
            className="inline-flex items-center gap-2 rounded border border-current/20 px-3 py-2 text-xs text-midground hover:bg-muted/10 transition-colors"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Browse Sessions
          </button>
        </div>

        {/* Keyboard hint */}
        <p className="text-[0.65rem] text-muted-foreground/40 pt-1">
          Press{" "}
          <kbd className="rounded border border-current/20 px-1 py-0.5 text-[0.6rem]">
            {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}K
          </kbd>{" "}
          for command palette
        </p>
      </div>
    </div>
  );
}
