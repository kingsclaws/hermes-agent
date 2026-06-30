import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  ClipboardCheck,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { cn } from "@/lib/utils";

/**
 * Expandable tool call row — the web equivalent of Ink's ToolTrail node.
 *
 * Renders one `tool.start` + `tool.complete` pair (plus any `tool.progress`
 * in between) as a single collapsible item in the transcript:
 *
 *   ▸ ● read_file(path=/foo)                         2.3s
 *
 * Click the header to reveal a preformatted body with context (args), the
 * streaming preview (while running), and the final summary or error. Error
 * rows auto-expand so failures aren't silently collapsed.
 */

export interface ToolEntry {
  kind: "tool";
  id: string;
  tool_id: string;
  name: string;
  context?: string;
  preview?: string;
  summary?: string;
  result_text?: string;
  error?: string;
  inline_diff?: string;
  status: "running" | "done" | "error";
  startedAt: number;
  completedAt?: number;
}

const STATUS_TONE: Record<ToolEntry["status"], string> = {
  running: "border-primary/40 bg-primary/[0.04]",
  done: "border-border bg-muted/20",
  error: "border-destructive/50 bg-destructive/[0.04]",
};

const BULLET_TONE: Record<ToolEntry["status"], string> = {
  running: "text-primary",
  done: "text-primary/80",
  error: "text-destructive",
};

const TICK_MS = 500;

export function ToolCall({ tool }: { tool: ToolEntry }) {
  // `open` is derived: errors default-expanded, everything else collapsed.
  // `null` means "follow the default"; any explicit bool is the user's override.
  // This lets a running tool flip to expanded automatically when it errors,
  // without mirroring state in an effect.
  const [userOverride, setUserOverride] = useState<boolean | null>(null);
  const open = userOverride ?? tool.status === "error";

  // Tick `now` while the tool is running so the elapsed label updates live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (tool.status !== "running") return;
    const id = window.setInterval(() => setNow(() => Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [tool.status]);

  // Historical tools (hydrated from session.resume) signal missing timestamps
  // with `startedAt === 0`; we hide the elapsed badge for those rather than
  // rendering a misleading "0ms".
  const hasTimestamps = tool.startedAt > 0;
  const elapsed = hasTimestamps
    ? fmtElapsed((tool.completedAt ?? now) - tool.startedAt)
    : null;

  const hasBody = !!(
    tool.context ||
    tool.preview ||
    tool.summary ||
    tool.result_text ||
    tool.error ||
    tool.inline_diff
  );

  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div
      className={`lex-tool-enter rounded-md border overflow-hidden ${STATUS_TONE[tool.status]} ${
        tool.status === "running" ? "lex-running-surface lex-soft-glow" : ""
      }`}
    >
      <button
        type="button"
        onClick={() => setUserOverride(!open)}
        disabled={!hasBody}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-foreground/2 disabled:cursor-default"
      >
        {hasBody ? (
          <Chevron className="h-3 w-3 shrink-0 text-muted-foreground" />
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <Zap className={`h-3 w-3 shrink-0 ${BULLET_TONE[tool.status]}`} />

        <span className="font-mono font-medium shrink-0">{tool.name}</span>

        <span className="font-mono text-text-secondary truncate min-w-0 flex-1">
          {tool.context ?? ""}
        </span>

        {tool.status === "running" && (
          <span
            className="lex-live-dot inline-block h-2 w-2 rounded-full bg-primary shrink-0"
            title="running"
          />
        )}
        {tool.status === "done" && tool.result_text && (
          <span className="shrink-0 rounded border border-primary/20 bg-primary/[0.04] px-1.5 py-0.5 font-mono text-[0.6rem] uppercase text-primary/80">
            output
          </span>
        )}
        {tool.status === "error" && (
          <AlertCircle
            className="h-3 w-3 shrink-0 text-destructive"
            aria-label="error"
          />
        )}
        {tool.status === "done" && (
          <Check
            className="h-3 w-3 shrink-0 text-primary/80"
            aria-label="done"
          />
        )}

        {elapsed && (
          <span className="font-mono text-xs text-text-tertiary tabular-nums shrink-0">
            {elapsed}
          </span>
        )}
      </button>

      {open && hasBody && (
        <div className="lex-panel-reveal border-t border-border/60 px-3 py-2 space-y-2 text-xs">
          {tool.context && (
            <Section label="context" monospace>
              {tool.context}
            </Section>
          )}

          {tool.preview && tool.status === "running" && (
            <Section label="streaming" monospace>
              {tool.preview}
              <span className="lex-stream-caret" />
            </Section>
          )}

          {tool.inline_diff && (
            <Section label="diff" monospace copyText={tool.inline_diff}>
              <pre className="whitespace-pre overflow-x-auto text-[0.7rem] leading-snug">
                {colorizeDiff(tool.inline_diff)}
              </pre>
            </Section>
          )}

          {tool.summary && (
            <Section label="summary" monospace copyText={tool.summary}>
              <span className="text-foreground/90 whitespace-pre-wrap">{tool.summary}</span>
            </Section>
          )}

          {tool.result_text && (
            <Section label="output" copyText={tool.result_text}>
              <div className="max-h-[28rem] overflow-y-auto rounded border border-current/10 bg-black/10 p-2.5">
                <Markdown content={tool.result_text} />
              </div>
            </Section>
          )}

          {tool.error && (
            <Section label="error" tone="error" monospace copyText={tool.error}>
              <span className="text-destructive whitespace-pre-wrap">
                {tool.error}
              </span>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Section({
  label,
  children,
  tone,
  copyText,
  monospace,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "error";
  copyText?: string;
  monospace?: boolean;
}) {
  return (
    <div className="flex gap-3">
      <div
        className={cn(
          "flex w-20 shrink-0 items-start gap-1 pt-0.5 text-xs",
          tone === "error" ? "text-destructive" : "text-text-tertiary",
        )}
      >
        <span className="text-display font-mondwest tracking-wider">{label}</span>
        {copyText && <CopyButton text={copyText} />}
      </div>

      <div className={cn("flex-1 min-w-0 text-muted-foreground", monospace && "font-mono")}>
        {children}
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="rounded p-0.5 text-text-tertiary hover:bg-muted/20 hover:text-foreground"
      title="Copy"
      aria-label="Copy tool output"
      onClick={(ev) => {
        ev.stopPropagation();
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? <ClipboardCheck className="h-3 w-3" /> : <Clipboard className="h-3 w-3" />}
    </button>
  );
}

function fmtElapsed(ms: number): string {
  const sec = Math.max(0, ms) / 1000;
  if (sec < 1) return `${Math.round(ms)}ms`;
  if (sec < 10) return `${sec.toFixed(1)}s`;
  if (sec < 60) return `${Math.round(sec)}s`;

  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** Colorize unified-diff lines for the inline diff section. */
function colorizeDiff(diff: string): React.ReactNode {
  return diff.split("\n").map((line, i) => (
    <div key={i} className={diffLineClass(line)}>
      {line || "\u00A0"}
    </div>
  ));
}

function diffLineClass(line: string): string {
  if (line.startsWith("+") && !line.startsWith("+++"))
    return "text-emerald-500 dark:text-emerald-400";
  if (line.startsWith("-") && !line.startsWith("---"))
    return "text-destructive";
  if (line.startsWith("@@")) return "text-primary";
  return "text-text-secondary";
}
