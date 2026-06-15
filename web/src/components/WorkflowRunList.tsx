import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Play,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SwarmRunSummary } from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowRunListProps {
  board: string;
  /** If true, wrap in a card with title. Otherwise just the list. */
  titled?: boolean;
  /** Max runs to show. */
  limit?: number;
  /** Callback when runs are loaded. */
  onRunsLoaded?: (runs: SwarmRunSummary[]) => void;
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_ICONS: Record<string, typeof CheckCircle2> = {
  done: CheckCircle2,
  running: RefreshCw,
  ready: Play as unknown as typeof CheckCircle2,  // won't be used
  pending: Clock,
  blocked: AlertTriangle,
};

const STATUS_TONES: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  done: "success",
  running: "warning",
  blocked: "destructive",
  pending: "secondary",
};

function RunStatusIcon({ status }: { status: string }) {
  const Icon = STATUS_ICONS[status] ?? Clock;
  const cls = status === "running" ? "animate-spin" : "";
  return <Icon className={`w-3.5 h-3.5 ${cls}`} />;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function WorkflowRunList({
  board,
  titled = false,
  limit,
  onRunsLoaded,
}: WorkflowRunListProps) {
  const [runs, setRuns] = useState<SwarmRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    if (!board) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchSwarmRuns(board);
      const list = data.runs ?? [];
      setRuns(list);
      onRunsLoaded?.(list);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load runs");
    } finally {
      setLoading(false);
    }
  }, [board, onRunsLoaded]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-refresh when any run is active
  useEffect(() => {
    const hasActive = runs.some(
      (r) => r.status === "running" || r.status === "ready",
    );
    if (!hasActive) return;
    const timer = setInterval(load, 20000);
    return () => clearInterval(timer);
  }, [runs, load]);

  // ── States ────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Spinner className="text-lg text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-8 gap-2">
        <AlertTriangle className="w-4 h-4 text-destructive" />
        <p className="text-xs text-destructive">{error}</p>
      </div>
    );
  }

  const displayed = limit ? runs.slice(0, limit) : runs;

  const content = (
    <>
      {displayed.length === 0 ? (
        <p className="text-xs text-secondary py-4 text-center">
          No swarm runs on this board.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {displayed.map((run) => (
            <div
              key={run.run_id}
              className="flex items-center gap-2 px-3 py-2 rounded-md hover:bg-secondary/5 cursor-pointer group"
              onClick={() =>
                navigate(
                  `/swarm-board?board=${encodeURIComponent(board)}&run=${encodeURIComponent(run.run_id)}`,
                )
              }
            >
              <RunStatusIcon status={run.status} />
              <span className="text-xs truncate flex-1 min-w-0">
                {run.workflow_id}
              </span>
              <span className="text-[10px] text-secondary shrink-0">
                {run.node_count} nodes
              </span>
              <span className="text-[10px] text-secondary shrink-0">
                {timeAgo(run.created_at)}
              </span>
              <ChevronRight className="w-3 h-3 text-secondary opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
            </div>
          ))}
        </div>
      )}
    </>
  );

  if (!titled) return content;

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-secondary uppercase tracking-wide">
            Swarm Runs
          </h3>
          <Badge tone="secondary" className="text-[10px]">
            {runs.length}
          </Badge>
        </div>
        {content}
      </CardContent>
    </Card>
  );
}
