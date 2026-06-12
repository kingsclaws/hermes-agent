import { Button } from "@nous-research/ui/ui/components/button";
import { GatewayClient } from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";
import { ArrowRight, Search, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

interface CommandDef {
  command: string;
  description: string;
  category: string;
}

const BUILTIN_COMMANDS: CommandDef[] = [
  { command: "/help", description: "Show help and available commands", category: "General" },
  { command: "/clear", description: "Clear conversation context", category: "General" },
  { command: "/model", description: "Switch the active model", category: "Model" },
  { command: "/fast", description: "Toggle fast mode (Opus with faster output)", category: "Model" },
  { command: "/resume", description: "Resume a previous session by ID", category: "Session" },
  { command: "/copy", description: "Copy last assistant response", category: "Session" },
  { command: "/config", description: "View or change configuration", category: "Config" },
  { command: "/theme", description: "Switch the UI theme", category: "Config" },
  { command: "/status", description: "Show system status", category: "General" },
  { command: "/todos", description: "View task list", category: "Tasks" },
  { command: "/review", description: "Start a document review", category: "Legal" },
  { command: "/draft", description: "Start document drafting workflow", category: "Legal" },
  { command: "/proofread", description: "Start proofreading workflow", category: "Legal" },
  { command: "/xref", description: "Run cross-reference audit", category: "Legal" },
  { command: "/deliver", description: "Create delivery package", category: "Legal" },
  { command: "/diff", description: "Show document diff", category: "Legal" },
  { command: "/ocr", description: "Run OCR on a document", category: "Legal" },
  { command: "/project", description: "Manage legal projects", category: "Legal" },
  { command: "/role", description: "Switch active agent role", category: "Agent" },
  { command: "/delegate", description: "Delegate a task to a subagent", category: "Agent" },
];

interface CommandPaletteProps {
  gw: GatewayClient | null;
  sessionId: string | null;
  onExecute: (command: string) => void;
}

export function CommandPalette({ onExecute }: CommandPaletteProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.toLowerCase().replace(/^\//, "");
    if (!q) return BUILTIN_COMMANDS;
    return BUILTIN_COMMANDS.filter(
      (c) =>
        c.command.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q),
    );
  }, [query]);

  // Cmd+K / Ctrl+K to toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const execute = useCallback(
    (command: string) => {
      setOpen(false);
      onExecute(command);
    },
    [onExecute],
  );

  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelected((s) => (s + 1) % Math.max(1, filtered.length));
          break;
        case "ArrowUp":
          e.preventDefault();
          setSelected((s) => (s - 1 + filtered.length) % Math.max(1, filtered.length));
          break;
        case "Enter":
          e.preventDefault();
          if (filtered[selected]) execute(filtered[selected].command);
          break;
        case "Escape":
          e.preventDefault();
          setOpen(false);
          break;
      }
    },
    [filtered, selected, execute],
  );

  if (!open) return null;

  const categories = [...new Set(filtered.map((c) => c.category))];

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={() => setOpen(false)}
      />

      <div className="relative z-10 w-full max-w-lg rounded-lg border border-primary/20 bg-background-base shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 border-b border-current/10 px-3 py-2">
          <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            onKeyDown={handleKey}
            placeholder="Search commands..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center gap-1">
            <kbd className="rounded border border-current/20 px-1.5 py-0.5 text-[0.6rem] text-muted-foreground font-mono">
              esc
            </kbd>
            <Button
              ghost
              size="icon"
              onClick={() => setOpen(false)}
              className="h-6 w-6"
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        </div>

        <div className="max-h-72 overflow-y-auto p-1">
          {filtered.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">
              No commands match "{query}"
            </div>
          )}

          {categories.map((cat) => {
            const items = filtered.filter((c) => c.category === cat);
            if (!items.length) return null;
            return (
              <div key={cat}>
                <div className="px-3 pt-2 pb-0.5 text-[0.6rem] uppercase tracking-wider text-muted-foreground">
                  {cat}
                </div>
                {items.map((cmd) => {
                  const idx = filtered.indexOf(cmd);
                  const active = idx === selected;
                  return (
                    <button
                      key={cmd.command}
                      type="button"
                      onClick={() => execute(cmd.command)}
                      onMouseEnter={() => setSelected(idx)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm transition-colors",
                        active
                          ? "bg-primary/10 text-primary"
                          : "text-foreground hover:bg-muted/10",
                      )}
                    >
                      <span className="font-mono text-xs shrink-0 min-w-[6rem]">
                        {cmd.command}
                      </span>
                      <span className="truncate text-muted-foreground">
                        {cmd.description}
                      </span>
                      {active && (
                        <ArrowRight className="h-3 w-3 shrink-0 ml-auto text-primary" />
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>

        <div className="border-t border-current/10 px-3 py-1.5 text-[0.6rem] text-muted-foreground">
          <kbd className="rounded border border-current/20 px-1 py-0.5 font-mono">↑↓</kbd>{" "}
          navigate ·{" "}
          <kbd className="rounded border border-current/20 px-1 py-0.5 font-mono">↵</kbd>{" "}
          select ·{" "}
          <kbd className="rounded border border-current/20 px-1 py-0.5 font-mono">esc</kbd>{" "}
          dismiss
        </div>
      </div>
    </div>
  );
}
