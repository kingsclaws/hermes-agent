import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  Circle,
  Clock,
  Play,
  RefreshCw,
  Users,
  AlertTriangle,
  Building2,
  ChevronRight,
  KanbanSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  SwarmRunStatusResponse,
  SwarmNodeStatus,
  SwarmNodeTask,
  ProjectInfo,
  WorkflowDefinition,
  SwarmRunSummary,
} from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Segmented } from "@nous-research/ui/ui/components/segmented";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { PluginSlot } from "@/plugins";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_ICONS: Record<string, typeof CheckCircle2> = {
  done: CheckCircle2,
  running: RefreshCw,
  ready: Play,
  pending: Clock,
  todo: Circle,
  blocked: AlertTriangle,
};

const STATUS_TONES: Record<string, "success" | "warning" | "secondary" | "destructive"> = {
  done: "success",
  running: "warning",
  ready: "secondary",
  pending: "secondary",
  todo: "secondary",
  blocked: "destructive",
};

const COLUMNS = [
  { key: "ready", label: "Ready", icon: Play },
  { key: "running", label: "Running", icon: RefreshCw },
  { key: "done", label: "Done", icon: CheckCircle2 },
  { key: "todo", label: "Todo", icon: Circle },
  { key: "blocked", label: "Blocked", icon: AlertTriangle },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SwarmBoardPage() {
  const [searchParams] = useSearchParams();
  const board = searchParams.get("board") || "";
  const runId = searchParams.get("run") || "";

  const [status, setStatus] = useState<SwarmRunStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"board" | "list">("board");

  const { setAfterTitle, setEnd, setTitle } = usePageHeader();
  const navigate = useNavigate();
  const { showToast } = useToast();

  // ── Launcher state (when no board/run in URL) ──────────────────────────

  const [allRuns, setAllRuns] = useState<SwarmRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [workflowDefs, setWorkflowDefs] = useState<WorkflowDefinition[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (board && runId) return;

    api.fetchAllSwarmRuns().then((d) => {
      setAllRuns(d.runs ?? []);
    }).catch(() => {}).finally(() => setRunsLoading(false));

    api.fetchProjects().then((d) => {
      setProjects(d.projects ?? []);
      if ((d.projects ?? []).length > 0) setSelectedProjectId(d.projects[0].id);
    }).catch(() => {});

    api.fetchWorkflowDefinitions().then((d) => {
      setWorkflowDefs(d.definitions ?? []);
      if ((d.definitions ?? []).length > 0) setSelectedWorkflowId(d.definitions[0].id);
    }).catch(() => {});
  }, [board, runId]);

  const handleLaunch = useCallback(async () => {
    if (!selectedProjectId || !selectedWorkflowId || launching) return;
    setLaunching(true);
    try {
      const result = await api.compileSwarmWorkflow(selectedProjectId, selectedWorkflowId);
      navigate(
        `/swarm-board?board=${encodeURIComponent(result.board)}&run=${encodeURIComponent(result.run_id)}`,
      );
    } catch (e: any) {
      showToast(e?.message ?? "Failed to compile workflow", "error");
      setLaunching(false);
    }
  }, [selectedProjectId, selectedWorkflowId, launching, navigate, showToast]);

  const handleViewRun = useCallback((run: SwarmRunSummary) => {
    navigate(
      `/swarm-board?board=${encodeURIComponent(run.board)}&run=${encodeURIComponent(run.run_id)}`,
    );
  }, [navigate]);

  // ── Load ──────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    if (!board || !runId) {
      setError("Missing board or run query parameters");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchSwarmRunStatus(runId, board);
      setStatus(data);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load swarm run");
    } finally {
      setLoading(false);
    }
  }, [board, runId]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-refresh every 15s when run is active
  useEffect(() => {
    if (!status?.nodes) return;
    const hasActive = Object.values(status.nodes).some(
      (n: SwarmNodeStatus) => n.status === "running" || n.status === "ready",
    );
    if (!hasActive) return;
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [status, load]);

  // ── Page header ──────────────────────────────────────────────────

  useLayoutEffect(() => {
    if (loading) {
      setAfterTitle(null);
      setEnd(null);
      return;
    }
    if (status?.run) {
      setTitle(
        `${status.run.workflow_id} / ${status.run.run_id?.slice(0, 12) || "?"}`,
      );
    }
    setAfterTitle(
      <Badge tone={status && !status.ok ? "destructive" : "secondary"}>
        {status?.run?.node_count ?? 0} nodes
      </Badge>,
    );
    setEnd(
      <div className="flex items-center gap-2">
        <Segmented
          value={view}
          onChange={(v) => setView(v as "board" | "list")}
          options={[
            { value: "board", label: "Board" },
            { value: "list", label: "List" },
          ]}
        />
        <Button ghost size="xs" onClick={load}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
      setTitle(null);
    };
  }, [loading, status, view, load]);

  // ── Derived data ─────────────────────────────────────────────────

  const nodesByColumn = useMemo(() => {
    if (!status?.nodes) return {};
    const grouped: Record<string, SwarmNodeStatus[]> = {};
    for (const col of COLUMNS) grouped[col.key] = [];
    for (const node of Object.values(status.nodes)) {
      const st = node.status || "pending";
      const col = st === "todo" ? "todo" : st === "blocked" ? "blocked" : st;
      if (grouped[col]) grouped[col].push(node);
    }
    return grouped;
  }, [status]);

  const progress = useMemo(() => {
    if (!status?.nodes) return 0;
    const all = Object.values(status.nodes);
    if (all.length === 0) return 0;
    const done = all.filter((n: SwarmNodeStatus) => n.status === "done").length;
    return Math.round((done / all.length) * 100);
  }, [status]);

  // ── Launcher (no board/run params) ──────────────────────────────────

  if (!board || !runId) {
    const selectedWf = workflowDefs.find((w) => w.id === selectedWorkflowId);
    return (
      <div className="flex flex-col items-center justify-center h-full overflow-y-auto py-8 gap-6">
        <div className="w-full max-w-2xl px-4">
          <div className="text-center mb-6">
            <KanbanSquare className="w-10 h-10 text-primary/50 mx-auto mb-3" />
            <h2 className="text-lg font-semibold mb-1">Legal Swarm Kanban</h2>
            <p className="text-sm text-secondary">
              Real-time kanban board for legal swarm workflow runs — watch bots work through columns.
            </p>
          </div>

          {/* Existing Runs */}
          <Card className="mb-4">
            <CardContent className="p-4">
              <h3 className="text-xs font-semibold text-secondary uppercase tracking-wide mb-3">
                Active Runs
              </h3>
              {runsLoading ? (
                <div className="flex justify-center py-4">
                  <Spinner className="text-primary" />
                </div>
              ) : allRuns.length === 0 ? (
                <p className="text-xs text-secondary text-center py-4">
                  No runs yet. Start one below.
                </p>
              ) : (
                <div className="flex flex-col gap-1 max-h-60 overflow-y-auto">
                  {allRuns.map((run) => (
                    <button
                      key={run.run_id || run.root_task_id}
                      type="button"
                      className="flex items-center gap-3 px-3 py-2 rounded-md text-left hover:bg-secondary/5 border border-transparent hover:border-border transition-colors w-full"
                      onClick={() => handleViewRun(run)}
                    >
                      <Badge
                        tone={run.status === "done" ? "success" : run.status === "running" ? "warning" : "secondary"}
                        className="text-[10px] shrink-0"
                      >
                        {run.status ?? "todo"}
                      </Badge>
                      <span className="text-sm font-medium truncate min-w-0">
                        {run.workflow_id || "?"}
                      </span>
                      <span className="text-[10px] text-secondary font-mono-ui shrink-0">
                        {run.run_id?.slice(0, 12) || run.root_task_id.slice(0, 12)}
                      </span>
                      <span className="text-[10px] text-secondary ml-auto shrink-0">
                        {run.node_count} nodes
                      </span>
                      <ChevronRight className="w-3.5 h-3.5 text-secondary shrink-0" />
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* New Run */}
          <Card>
            <CardContent className="p-4 flex flex-col gap-4">
              <h3 className="text-xs font-semibold text-secondary uppercase tracking-wide">
                Start New Run
              </h3>

              <div>
                <label className="text-xs font-semibold text-secondary uppercase tracking-wide block mb-1.5">
                  Project
                </label>
                <div className="flex flex-col gap-1 max-h-36 overflow-y-auto">
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={cn(
                        "flex items-center gap-2 px-3 py-2 rounded-md text-left text-sm transition-colors w-full",
                        p.id === selectedProjectId
                          ? "bg-primary/10 border border-primary/30"
                          : "hover:bg-secondary/5 border border-transparent",
                      )}
                      onClick={() => setSelectedProjectId(p.id)}
                    >
                      <Building2 className="w-3.5 h-3.5 text-secondary shrink-0" />
                      <span className="truncate">{p.name || p.id}</span>
                      {p.id === selectedProjectId && (
                        <ChevronRight className="w-3.5 h-3.5 text-primary shrink-0 ml-auto" />
                      )}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-secondary uppercase tracking-wide block mb-1.5">
                  Workflow
                </label>
                <div className="flex flex-col gap-1">
                  {workflowDefs.map((wf) => (
                    <button
                      key={wf.id}
                      type="button"
                      className={cn(
                        "flex flex-col px-3 py-2 rounded-md text-left transition-colors w-full",
                        wf.id === selectedWorkflowId
                          ? "bg-primary/10 border border-primary/30"
                          : "hover:bg-secondary/5 border border-transparent",
                      )}
                      onClick={() => setSelectedWorkflowId(wf.id)}
                    >
                      <span className="text-sm font-medium">{wf.id}</span>
                      <span className="text-[11px] text-secondary">{wf.pipeline}</span>
                    </button>
                  ))}
                </div>
              </div>

              <Button
                onClick={handleLaunch}
                disabled={!selectedProjectId || !selectedWorkflowId || launching}
                className="w-full"
                prefix={launching ? <Spinner className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              >
                {launching ? "Compiling..." : "Start New Run"}
              </Button>
              {selectedWf && (
                <p className="text-[10px] text-secondary text-center -mt-2">
                  {selectedWf.node_count} bots &middot; {selectedWf.timeout_minutes}min timeout
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // ── Loading / Error states ──────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-2">
        <AlertTriangle className="w-8 h-8 text-destructive opacity-60" />
        <p className="text-sm text-destructive">{error}</p>
        <Button ghost onClick={load}>
          Retry
        </Button>
      </div>
    );
  }

  if (!status || !status.ok) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-2">
        <p className="text-sm text-destructive">
          {status?.error || "Run not found"}
        </p>
      </div>
    );
  }

  // ── Render board view ────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4 h-full">
      <PluginSlot name="swarmboard:top" />

      {/* Progress bar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-2 bg-secondary/15 rounded-full overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="text-xs text-secondary font-mono-ui">{progress}%</span>
      </div>

      {view === "board" ? (
        /* ── Kanban board columns ──────────────────────────── */
        <div className="flex gap-3 flex-1 min-h-0 overflow-x-auto">
          {COLUMNS.map((col) => {
            const nodes = nodesByColumn[col.key] || [];
            return (
              <div
                key={col.key}
                className="flex-1 min-w-[200px] max-w-[300px] flex flex-col gap-2"
              >
                <div className="flex items-center gap-1.5 px-1">
                  <col.icon className="w-3.5 h-3.5 text-secondary" />
                  <span className="text-xs font-semibold text-secondary uppercase tracking-wide">
                    {col.label}
                  </span>
                  <Badge tone="secondary" className="text-[10px] ml-auto">
                    {nodes.length}
                  </Badge>
                </div>
                <div className="flex flex-col gap-2 flex-1 min-h-0 overflow-y-auto p-1">
                  {nodes.map((node) => (
                    <Card key={node.node_id} className="border-border">
                      <CardContent className="p-3">
                        <div className="flex items-start justify-between gap-2 mb-1">
                          <p className="text-sm font-semibold">{node.node_id}</p>
                          <Badge
                            tone={STATUS_TONES[node.status] ?? "secondary"}
                            className="text-[10px]"
                          >
                            {node.status}
                          </Badge>
                        </div>
                        <p className="text-[10px] text-secondary mb-1">
                          {node.kind}
                        </p>
                        {node.tasks.map((task: SwarmNodeTask) => (
                          <div
                            key={task.task_id}
                            className="flex items-center gap-1.5 mt-1"
                          >
                            {task.status === "done" ? (
                              <CheckCircle2 className="w-3 h-3 text-success" />
                            ) : task.status === "running" ? (
                              <RefreshCw className="w-3 h-3 text-warning animate-spin" />
                            ) : (
                              <Circle className="w-3 h-3 text-secondary" />
                            )}
                            <span className="text-[11px] truncate flex-1">
                              {task.title || task.task_id?.slice(0, 16)}
                            </span>
                            <span className="text-[10px] text-secondary flex items-center gap-0.5 shrink-0">
                              <Users className="w-2.5 h-2.5" />
                              {task.assignee || "?"}
                            </span>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  ))}
                  {nodes.length === 0 && (
                    <p className="text-[10px] text-secondary text-center py-4">
                      No tasks
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* ── List view ────────────────────────────────────── */
        <div className="flex flex-col gap-2 flex-1 min-h-0 overflow-y-auto">
          {Object.values(status.nodes).map((node: SwarmNodeStatus) => {
            const Icon = STATUS_ICONS[node.status] ?? Circle;
            return (
              <div
                key={node.node_id}
                className="flex items-center gap-3 px-3 py-2 rounded-md border border-border hover:bg-secondary/5"
              >
                <Icon
                  className={[
                    "w-4 h-4 shrink-0",
                    node.status === "running" ? "animate-spin" : "",
                  ].join(" ")}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{node.node_id}</span>
                    <Badge
                      tone={STATUS_TONES[node.status] ?? "secondary"}
                      className="text-[10px]"
                    >
                      {node.status}
                    </Badge>
                    <span className="text-[10px] text-secondary">{node.kind}</span>
                  </div>
                  <div className="flex gap-2 mt-0.5">
                    {node.tasks.map((task: SwarmNodeTask) => (
                      <span
                        key={task.task_id}
                        className="text-[10px] text-secondary flex items-center gap-1"
                      >
                        <Users className="w-2.5 h-2.5" />
                        {task.assignee || "?"}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <PluginSlot name="swarmboard:bottom" />
    </div>
  );
}
