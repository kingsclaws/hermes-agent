import { Brain, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface ThinkingBlockProps {
  text: string;
  defaultOpen?: boolean;
}

/**
 * Expandable thinking/reasoning section rendered inline in the message stream.
 * Mirrors Claude Code's expandable "Thinking" section showing the model's
 * internal reasoning before the final answer.
 */
export function ThinkingBlock({ text, defaultOpen = false }: ThinkingBlockProps) {
  const [open, setOpen] = useState(defaultOpen);
  const preview = text.slice(0, 120);

  if (!text) return null;

  return (
    <div className="rounded-md border border-primary/20 bg-primary/[0.03] overflow-hidden my-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-xs hover:bg-primary/[0.06] transition-colors"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-primary/70" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-primary/70" />
        )}
        <Brain className="h-3 w-3 shrink-0 text-primary/70" />
        <span className="font-medium text-primary/80">Thinking</span>
        {!open && (
          <span className="truncate text-muted-foreground ml-1">
            {preview}{text.length > 120 ? "..." : ""}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-primary/10 px-3 py-2">
          <pre className="whitespace-pre-wrap text-xs text-muted-foreground leading-relaxed font-sans">
            {text}
          </pre>
        </div>
      )}
    </div>
  );
}

interface ThinkingStreamProps {
  blocks: ThinkingBlockData[];
  className?: string;
}

export interface ThinkingBlockData {
  id: string;
  text: string;
  complete: boolean;
}

/**
 * Renders a list of thinking blocks (one per reasoning round).
 * Streams the current incomplete block by default-open.
 */
export function ThinkingStream({ blocks, className }: ThinkingStreamProps) {
  if (!blocks.length) return null;

  return (
    <div className={cn("space-y-1", className)}>
      {blocks.map((block) => (
        <ThinkingBlock
          key={block.id}
          text={block.text}
          defaultOpen={!block.complete}
        />
      ))}
    </div>
  );
}
