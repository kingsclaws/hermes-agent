import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  X,
  Loader2,
  CheckCircle2,
  Circle,
  AlertCircle,
  PlayCircle,
  Clock,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SwarmRunStatusResponse, SwarmNodeStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

export interface CompactKanbanPanelProps {
  board: string;
  runId: string;
  onClose?: () => void;
}

const COLUMN_ORDER = ["running", "ready", "todo", "blocked", "done"] as const;

const COLUMN_CONFIG: Record<
  string,
  { label: string; icon: typeof Circle; color: string; dot: string }
> = {
  running: { label: "Running", icon: PlayCircle, color: "text-blue-400", dot: "bg-blue-400" },
  ready: { label: "Ready", icon: Clock, color: "text-amber-400", dot: "bg-amber-400" },
  todo: { label: "Todo", icon: Circle, color: "text-text-tertiary", dot: "bg-text-tertiary" },
  blocked: { label: "Blocked", icon: AlertCircle, color: "text-red-400", dot: "bg-red-400" },
  done: { label: "Done", icon: CheckCircle2, color: "text-green-400", dot: "bg-green-400" },
};

export function CompactKanbanPanel({ board, runId, onClose }: CompactKanbanPanelProps) {
  const [status, setStatus] = useState<SwarmRunStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(["done"]));
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.fetchSwarmRunStatus(runId, board);
      setStatus(data);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [runId, board]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const hasActive = useMemo(() => {
    if (!status?.nodes) return false;
    return Object.values(status.nodes).some(
      (n) => n.status === "running" || n.status === "ready",
    );
  }, [status]);

  useEffect(() => {
    if (!hasActive) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }
    intervalRef.current = setInterval(load, 10_000);
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [hasActive, load]);

  const nodesByColumn = useMemo(() => {
    if (!status?.nodes) return {};
    const groups: Record<string, SwarmNodeStatus[]> = {};
    for (const node of Object.values(status.nodes)) {
      const col = COLUMN_ORDER.includes(node.status as any) ? node.status : "todo";
      (groups[col] ??= []).push(node);
    }
    return groups;
  }, [status]);

  const { total, done: doneCount } = useMemo(() => {
    if (!status?.nodes) return { total: 0, done: 0 };
    const nodes = Object.values(status.nodes);
    return {
      total: nodes.length,
      done: nodes.filter((n) => n.status === "done").length,
    };
  }, [status]);

  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  const toggleCollapse = useCallback((col: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(col)) next.delete(col);
      else next.add(col);
      return next;
    });
  }, []);

  if (loading) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader
          workflowId={null}
          onClose={onClose}
        />
        <div className="flex flex-1 items-center justify-center">
          <Spinner className="text-primary" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col">
        <PanelHeader workflowId={null} onClose={onClose} />
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-3">
          <p className="text-xs text-destructive">{error}</p>
          <Button ghost size="xs" onClick={load}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PanelHeader
        workflowId={status?.workflow_id ?? null}
        onClose={onClose}
      />

      {/* Progress bar */}
      <div className="shrink-0 px-3 pb-2">
        <div className="flex items-center justify-between text-[10px] text-text-tertiary mb-1">
          <span>{doneCount}/{total} nodes</span>
          <span>{pct}%</span>
        </div>
        <div className="h-1 w-full rounded-full bg-current/10 overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              pct === 100 ? "bg-green-500" : "bg-primary",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Column sections */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-1">
        {COLUMN_ORDER.map((col) => {
          const nodes = nodesByColumn[col];
          if (!nodes || nodes.length === 0) return null;
          const cfg = COLUMN_CONFIG[col];
          const Icon = cfg.icon;
          const isCollapsed = collapsed.has(col);

          return (
            <div key={col} className="mb-1">
              <button
                type="button"
                onClick={() => toggleCollapse(col)}
                className={cn(
                  "flex w-full items-center gap-1.5 px-2 py-1 text-[11px] font-medium tracking-wide",
                  "hover:bg-current/5 rounded transition-colors",
                  cfg.color,
                )}
              >
                {isCollapsed ? (
                  <ChevronRight className="h-3 w-3 shrink-0" />
                ) : (
                  <ChevronDown className="h-3 w-3 shrink-0" />
                )}
                <Icon className="h-3 w-3 shrink-0" />
                <span className="uppercase">{cfg.label}</span>
                <span className="ml-auto text-text-tertiary">({nodes.length})</span>
              </button>

              {!isCollapsed && (
                <div className="flex flex-col gap-0.5 px-1 pb-1">
                  {nodes.map((node) => (
                    <NodeCard key={node.node_id} node={node} dotColor={cfg.dot} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Auto-refresh indicator */}
      {hasActive && (
        <div className="shrink-0 flex items-center gap-1.5 px-3 py-1 text-[10px] text-text-tertiary border-t border-current/10">
          <Loader2 className="h-2.5 w-2.5 animate-spin" />
          <span>Auto-refreshing</span>
        </div>
      )}
    </div>
  );
}

function PanelHeader({
  workflowId,
  onClose,
}: {
  workflowId: string | null;
  onClose?: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-current/15 px-3 py-2">
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-[0.1em] text-text-tertiary">
          Swarm Progress
        </div>
        {workflowId && (
          <div className="text-xs font-medium text-text-secondary truncate">
            {workflowId}
          </div>
        )}
      </div>
      {onClose && (
        <Button ghost size="icon" onClick={onClose} className="shrink-0 h-6 w-6">
          <X className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );
}

function NodeCard({ node, dotColor }: { node: SwarmNodeStatus; dotColor: string }) {
  const assignees = node.tasks
    .map((t) => t.assignee)
    .filter((a) => a && a !== "unassigned");
  const uniqueAssignees = [...new Set(assignees)];

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded px-2 py-1.5",
        "bg-current/[0.03] border border-current/8",
        "text-xs text-text-secondary",
      )}
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotColor)} />
      <span className="truncate flex-1 font-medium">{node.node_id}</span>
      {uniqueAssignees.length > 0 && (
        <span className="shrink-0 text-[10px] text-text-tertiary truncate max-w-[80px]">
          {uniqueAssignees.join(", ")}
        </span>
      )}
    </div>
  );
}
