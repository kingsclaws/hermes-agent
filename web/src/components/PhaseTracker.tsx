import { cn } from "@/lib/utils";
import { Circle, CheckCircle2 } from "lucide-react";

interface PhaseTrackerProps {
  phases: string[];
  currentIndex: number;
  phaseHistory: Array<{ phase: string; completed: string }>;
  onAdvance: () => void;
  advancing: boolean;
}

const PHASE_LABELS: Record<string, string> = {
  init: "Init",
  drafting: "Drafting",
  review: "Review",
  execution: "Execution",
  cp: "CP",
  closing: "Closing",
  registration: "Registration",
};

export function PhaseTracker({ phases, currentIndex, phaseHistory, onAdvance, advancing }: PhaseTrackerProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-text-secondary">
        <span>Phase {currentIndex + 1}/{phases.length}</span>
        <span className="font-medium capitalize">
          {PHASE_LABELS[phases[currentIndex]] ?? phases[currentIndex]}
        </span>
      </div>
      <div className="flex items-center gap-1">
        {phases.map((p, i) => (
          <div
            key={p}
            className={cn(
              "flex-1 h-1.5 rounded-full transition-colors",
              i < currentIndex
                ? "bg-primary"
                : i === currentIndex
                  ? "bg-primary/60"
                  : "bg-muted/30",
            )}
            title={PHASE_LABELS[p] ?? p}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-1 mt-1">
        {phaseHistory.slice(-3).map((h, i) => (
          <span key={i} className="text-xs text-text-tertiary">
            <CheckCircle2 className="h-3 w-3 inline mr-0.5 text-primary" />
            {PHASE_LABELS[h.phase] ?? h.phase}
          </span>
        ))}
      </div>
      {currentIndex + 1 < phases.length && (
        <button
          type="button"
          onClick={onAdvance}
          disabled={advancing}
          className="text-xs text-primary hover:underline text-left disabled:opacity-50"
        >
          {advancing ? "Advancing..." : `Advance to ${PHASE_LABELS[phases[currentIndex + 1]] ?? phases[currentIndex + 1]}`}
        </button>
      )}
    </div>
  );
}
