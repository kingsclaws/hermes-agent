import { useCallback, useRef, useState, useEffect } from "react";
import { Brain, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type ReasoningEffort = "auto" | "low" | "medium" | "high" | "max";

const EFFORT_LEVELS: { value: ReasoningEffort; label: string; description: string }[] = [
  { value: "auto", label: "Auto", description: "Model decides" },
  { value: "low", label: "Low", description: "Fast, concise" },
  { value: "medium", label: "Med", description: "Balanced" },
  { value: "high", label: "High", description: "Thorough" },
  { value: "max", label: "Max", description: "Deep reasoning" },
];

const EFFORT_COLORS: Record<ReasoningEffort, string> = {
  auto: "text-muted-foreground",
  low: "text-blue-400",
  medium: "text-amber-400",
  high: "text-orange-400",
  max: "text-red-400",
};

interface ReasoningEffortPickerProps {
  value: ReasoningEffort;
  onChange: (effort: ReasoningEffort) => void;
  disabled?: boolean;
  className?: string;
}

export function ReasoningEffortPicker({
  value,
  onChange,
  disabled,
  className,
}: ReasoningEffortPickerProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const toggle = useCallback(() => {
    if (!disabled) setOpen((prev) => !prev);
  }, [disabled]);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const current = EFFORT_LEVELS.find((l) => l.value === value) ?? EFFORT_LEVELS[0];

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        aria-label={`Reasoning effort: ${current.label}`}
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1 rounded border border-current/15 px-2 py-1 text-[0.65rem] uppercase tracking-wider transition-colors",
          "hover:border-primary/40 hover:bg-primary/5",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          EFFORT_COLORS[value],
        )}
      >
        <Brain className="h-3 w-3" />
        <span>{current.label}</span>
        <ChevronDown className={cn("h-2.5 w-2.5 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1 w-44 rounded border border-current/20 bg-background-base/95 backdrop-blur-sm shadow-lg py-1">
          {EFFORT_LEVELS.map((level) => (
            <button
              key={level.value}
              type="button"
              onClick={() => {
                onChange(level.value);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center justify-between px-3 py-1.5 text-left text-xs transition-colors",
                "hover:bg-primary/10",
                value === level.value
                  ? EFFORT_COLORS[level.value]
                  : "text-text-secondary",
              )}
            >
              <span className="font-medium uppercase tracking-wider">{level.label}</span>
              <span className="text-[0.6rem] text-muted-foreground">{level.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
