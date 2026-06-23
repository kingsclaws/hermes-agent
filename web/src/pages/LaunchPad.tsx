import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  Search,
  FileText,
  Calendar,
  Building2,
  ChevronRight,
  Terminal,
  Send,
  Play,
  Plus,
  Trash2,
  X,
  FileCheck,
  Eye,
  ClipboardCheck,
  PackageOpen,
  MessagesSquare,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  ProjectDetail,
  SessionInfo,
  WorkflowDefinition,
  InventoryResponse,
} from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { useAsync } from "@/hooks/useAsync";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";

// ---------------------------------------------------------------------------
// Quick action pill
// ---------------------------------------------------------------------------

interface QuickAction {
  label: string;
  icon: typeof Terminal;
  prefix: string;
}

const QUICK_ACTIONS: QuickAction[] = [
  { label: "Draft", icon: FileCheck, prefix: "/draft " },
  { label: "Review", icon: Eye, prefix: "/review " },
  { label: "Format Check", icon: ClipboardCheck, prefix: "/format-check " },
  { label: "Deliver", icon: PackageOpen, prefix: "/deliver " },
];

// ---------------------------------------------------------------------------
// Create project form
// ---------------------------------------------------------------------------

interface CreateForm {
  project_name: string;
  client_name: string;
  goal: string;
  dir_path: string;
  language: string;
}

const EMPTY_FORM: CreateForm = {
  project_name: "",
  client_name: "",
  goal: "",
  dir_path: "/data/projects",
  language: "ch",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function LaunchPad() {
  const {
    data: projectsRaw, loading, error,
    refetch: loadProjects, setData: setProjects,
  } = useAsync(() => api.fetchProjects().then(d => d.projects ?? []));
  const projects = projectsRaw ?? [];
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Create project
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);

  const { showToast } = useToast();

  // Delete project
  const projectDelete = useConfirmDelete({
    onDelete: useCallback(
      async (id: string) => {
        try {
          await api.deleteProject(id);
          setProjects((prev) => (prev ?? []).filter((p) => p.id !== id));
          if (selectedId === id) setSelectedId(null);
          showToast("Project deleted", "success");
        } catch (e: any) {
          showToast(e?.message ?? "Delete failed", "error");
          throw new Error("delete failed");
        }
      },
      [selectedId, showToast],
    ),
  });

  // Context panel state
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null);
  const [inventory, setInventory] = useState<InventoryResponse | null>(null);
  const [workflowDefs, setWorkflowDefs] = useState<WorkflowDefinition[]>([]);
  const [contextLoading, setContextLoading] = useState(false);

  // Command bar
  const [commandText, setCommandText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [compiling, setCompiling] = useState<string | null>(null);

  const navigate = useNavigate();
  const { setAfterTitle, setEnd } = usePageHeader();


  // ── Page header ────────────────────────────────────────────────────

  useLayoutEffect(() => {
    if (loading) {
      setAfterTitle(null);
      setEnd(null);
      return;
    }
    setAfterTitle(
      <Badge tone="secondary">{projects.length} projects</Badge>,
    );
    setEnd(
      <div className="flex items-center gap-2">
        <div className="relative">
          <Search className="w-4 h-4 text-secondary absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none" />
          <Input
            placeholder="Search projects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-56 pl-8"
          />
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="h-3.5 w-3.5 mr-1" />
          New
        </Button>
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [loading, projects, search]);

  // ── Load project context on selection ──────────────────────────────

  useEffect(() => {
    if (!selectedId) {
      setProjectDetail(null);
      setInventory(null);
      setWorkflowDefs([]);
      return;
    }
    setContextLoading(true);
    Promise.all([
      api.getProject(selectedId).catch(() => null),
      api.fetchInventory(selectedId).catch(() => null),
      api.fetchWorkflowDefinitions().catch(() => ({ ok: true, definitions: [] })),
    ]).then(([detail, inv, wfDefs]) => {
      setProjectDetail(detail as ProjectDetail | null);
      setInventory(inv as InventoryResponse | null);
      setWorkflowDefs((wfDefs as any)?.definitions ?? []);
      setContextLoading(false);
    });
  }, [selectedId]);

  // ── Filtered projects ──────────────────────────────────────────────

  const filtered = search.trim()
    ? projects.filter((p) => {
        const s = search.toLowerCase();
        return (
          p.name?.toLowerCase().includes(s) ||
          p.client?.toLowerCase().includes(s) ||
          p.directory?.toLowerCase().includes(s)
        );
      })
    : projects;

  // ── Handlers ───────────────────────────────────────────────────────

  const handleCreate = useCallback(async () => {
    if (!createForm.project_name.trim() || !createForm.dir_path.trim()) return;
    setCreating(true);
    try {
      await api.createProject({
        project_name: createForm.project_name.trim(),
        client_name: createForm.client_name.trim(),
        goal: createForm.goal.trim(),
        dir_path: createForm.dir_path.trim(),
        language: createForm.language,
      });
      setShowCreate(false);
      setCreateForm(EMPTY_FORM);
      showToast("Project created", "success");
      await loadProjects();
    } catch (e: any) {
      showToast(e?.message ?? "Create failed", "error");
    } finally {
      setCreating(false);
    }
  }, [createForm, showToast, loadProjects]);

  const handleSend = useCallback(async () => {
    if (!selectedId || submitting) return;
    setSubmitting(true);
    try {
      const { session_id } = await api.createProjectSession(selectedId);
      navigate(`/chat?resume=${encodeURIComponent(session_id)}&project=${encodeURIComponent(selectedId)}`);
    } catch (e: any) {
      showToast(e?.message ?? "Failed to create session", "error");
      setSubmitting(false);
    }
  }, [selectedId, submitting, navigate, showToast]);

  const handleQuickAction = useCallback(
    (action: QuickAction) => {
      setCommandText(action.prefix);
    },
    [],
  );

  const handleRunWorkflow = useCallback(
    async (workflowId: string) => {
      if (!selectedId || compiling) return;
      setCompiling(workflowId);
      try {
        const result = await api.compileSwarmWorkflow(selectedId, workflowId);
        navigate(
          `/swarm-board?board=${encodeURIComponent(result.board)}&run=${encodeURIComponent(result.run_id)}&project=${encodeURIComponent(selectedId)}`,
        );
      } catch (e: any) {
        showToast(e?.message ?? "Failed to compile workflow", "error");
        setCompiling(null);
      }
    },
    [selectedId, compiling, navigate, showToast],
  );

  const handleOpenChatRoom = useCallback(
    async (workflowId: string) => {
      if (!selectedId || compiling) return;
      setCompiling(workflowId);
      try {
        const result = await api.createRoom(selectedId, workflowId);
        navigate(
          `/swarm-chat?board=${encodeURIComponent(result.board)}&run=${encodeURIComponent(result.run_id)}&project=${encodeURIComponent(selectedId)}`,
        );
      } catch (e: any) {
        showToast(e?.message ?? "Failed to create chat room", "error");
        setCompiling(null);
      }
    },
    [selectedId, compiling, navigate, showToast],
  );

  const handleKeyDown= useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  // ── Selected project ───────────────────────────────────────────────

  const selectedProject = projects.find((p) => p.id === selectedId) ?? null;
  const sessions = projectDetail?.sessions ?? [];
  const docs = inventory?.inventory?.documents
    ? Object.entries(inventory.inventory.documents).map(([path, entry]) => ({
        path,
        name: (entry as any).name ?? path.split("/").pop() ?? path,
        ...(entry as any),
      }))
    : [];

  // ── Loading state ──────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  // ── Error state ────────────────────────────────────────────────────

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-2">
        <p className="text-sm text-destructive">{error}</p>
        <Button ghost onClick={loadProjects}>
          Retry
        </Button>
      </div>
    );
  }

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 h-full">
      <PluginSlot name="launchpad:top" />
      <PluginSlot name="projects:top" />

      <DeleteConfirmDialog
        open={projectDelete.isOpen}
        onCancel={projectDelete.cancel}
        onConfirm={projectDelete.confirm}
        title="Delete Project"
        description="This will permanently delete the project and all its contents. This action cannot be undone."
        loading={projectDelete.isDeleting}
      />

      {/* ── Zone 1: Project card grid ─────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-secondary mb-3 tracking-wide uppercase">
          Projects
        </h2>
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-2 text-secondary">
            <Building2 className="w-8 h-8 opacity-40" />
            <p className="text-sm">No projects found</p>
            {search && (
              <Button ghost size="xs" onClick={() => setSearch("")}>
                Clear search
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {filtered.map((p) => (
              <Card
                key={p.id}
                className={cn(
                  "group cursor-pointer transition-all duration-100 hover:border-primary/40",
                  p.id === selectedId ? "border-primary ring-1 ring-primary/30" : "border-border",
                )}
                onClick={() => setSelectedId(p.id)}
              >
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold truncate">
                        {p.name || p.id}
                      </p>
                      {p.client && (
                        <p className="text-xs text-secondary mt-0.5 flex items-center gap-1">
                          <Building2 className="w-3 h-3 shrink-0" />
                          <span className="truncate">{p.client}</span>
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          projectDelete.requestDelete(p.id);
                        }}
                        className="p-1 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-destructive transition-opacity"
                        aria-label={`Delete ${p.name || p.id}`}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                      <ChevronRight
                        className={cn(
                          "w-4 h-4 shrink-0 transition-transform",
                          p.id === selectedId ? "text-primary rotate-90" : "text-secondary",
                        )}
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-3 mt-2 text-xs text-secondary">
                    {p.doc_count != null && p.doc_count > 0 && (
                      <span className="flex items-center gap-1">
                        <FileText className="w-3 h-3" />
                        {p.doc_count}
                      </span>
                    )}
                    {p.created && (
                      <span className="flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {timeAgo(new Date(p.created).getTime())}
                      </span>
                    )}
                  </div>
                  {p.status && (
                    <Badge
                      tone={p.status === "active" ? "success" : "secondary"}
                      className="mt-2 text-[10px]"
                    >
                      {p.status}
                    </Badge>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      {/* ── Zone 2: Context panel ─────────────────────────────── */}
      {selectedId && (
        <section className="flex gap-4 flex-1 min-h-0">
          {contextLoading ? (
            <div className="flex items-center justify-center py-8 w-full">
              <Spinner className="text-lg text-primary" />
            </div>
          ) : (
            <>
              {/* Sessions */}
              <div className="flex-1 min-w-0">
                <h3 className="text-xs font-semibold text-secondary mb-2 uppercase tracking-wide">
                  Recent Sessions
                </h3>
                {sessions.length === 0 ? (
                  <p className="text-xs text-secondary py-4">
                    No sessions yet. Create one to get started.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
                    {sessions.slice(0, 5).map((s: SessionInfo) => (
                      <ListItem
                        key={s.id}
                        onClick={() =>
                          navigate(
                            `/chat?resume=${encodeURIComponent(s.id)}&project=${encodeURIComponent(selectedId)}`,
                          )
                        }
                        className="cursor-pointer"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Terminal className="w-3.5 h-3.5 text-secondary shrink-0" />
                          <span className="text-xs truncate">
                            {s.title || s.id?.slice(0, 16) || "Untitled"}
                          </span>
                          <span className="text-[10px] text-secondary shrink-0 ml-auto">
                            {timeAgo(s.started_at)}
                          </span>
                        </div>
                      </ListItem>
                    ))}
                  </div>
                )}
              </div>

              {/* Workflows */}
              <div className="flex-1 min-w-0">
                <h3 className="text-xs font-semibold text-secondary mb-2 uppercase tracking-wide">
                  Swarm Workflows
                </h3>
                {workflowDefs.length === 0 ? (
                  <p className="text-xs text-secondary py-4">
                    No workflow definitions available.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
                    {workflowDefs.map((wf: WorkflowDefinition) => (
                      <div
                        key={wf.id}
                        className="flex items-center gap-2 px-3 py-1.5 rounded-md hover:bg-secondary/5"
                      >
                        <div className="min-w-0 flex-1">
                          <span className="text-xs font-medium truncate block">
                            {wf.id}
                          </span>
                          <span className="text-[10px] text-secondary truncate block">
                            {wf.pipeline}
                          </span>
                        </div>
                        <Badge tone="secondary" className="text-[10px] shrink-0">
                          {wf.node_count} nodes
                        </Badge>
                        <Button
                          size="xs"
                          ghost
                          onClick={() => handleRunWorkflow(wf.id)}
                          disabled={compiling === wf.id}
                          className="shrink-0"
                          title="Run workflow"
                        >
                          {compiling === wf.id ? (
                            <Spinner className="w-3 h-3" />
                          ) : (
                            <Play className="w-3 h-3" />
                          )}
                        </Button>
                        <Button
                          size="xs"
                          ghost
                          onClick={() => handleOpenChatRoom(wf.id)}
                          disabled={compiling === wf.id}
                          className="shrink-0"
                          title="Open chat room"
                        >
                          {compiling === wf.id ? (
                            <Spinner className="w-3 h-3" />
                          ) : (
                            <MessagesSquare className="w-3 h-3" />
                          )}
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Documents */}
              <div className="flex-1 min-w-0">
                <h3 className="text-xs font-semibold text-secondary mb-2 uppercase tracking-wide">
                  Documents
                </h3>
                {docs.length === 0 ? (
                  <p className="text-xs text-secondary py-4">
                    No documents in this project.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
                    {docs.slice(0, 8).map((doc: any) => (
                      <div
                        key={doc.path}
                        className="flex items-center gap-2 px-3 py-1 text-xs truncate"
                      >
                        <FileText className="w-3.5 h-3.5 text-secondary shrink-0" />
                        <span className="truncate">{doc.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      )}

      {/* ── Zone 3: Command bar ──────────────────────────────────────── */}
      <section className="mt-auto pt-2 border-t border-border">
        {/* Quick action pills */}
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          {QUICK_ACTIONS.map((action) => (
            <Button
              key={action.label}
              size="xs"
              ghost
              onClick={() => handleQuickAction(action)}
              className="text-xs"
            >
              <action.icon className="w-3.5 h-3.5" />
              {action.label}
            </Button>
          ))}
          <span className="text-secondary text-[10px]">or type a command:</span>
        </div>

        {/* Text input + Send */}
        <div className="flex items-center gap-2">
          <Input
            value={commandText}
            onChange={(e) => setCommandText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              selectedId
                ? "/draft 根据TS修改利率条款..."
                : "Select a project above, then type a command..."
            }
            disabled={!selectedId || submitting}
            className="flex-1 font-mono-ui text-sm"
          />
          <Button
            onClick={handleSend}
            disabled={!selectedId || submitting}
            prefix={submitting ? <Spinner className="w-4 h-4" /> : <Send className="w-4 h-4" />}
          >
            Launch
          </Button>
        </div>
        <p className="text-[10px] text-secondary mt-1.5">
          {selectedId
            ? `Selected: ${selectedProject?.name ?? selectedId} — a new session will be created`
            : "Select a project to begin"}
        </p>
      </section>

      <PluginSlot name="launchpad:bottom" />

      {/* Create Project Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setShowCreate(false)}
          />
          <div className="relative bg-card border border-border rounded-lg shadow-xl w-full max-w-lg mx-4 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Create New Project</h2>
              <Button ghost size="sm" onClick={() => setShowCreate(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="flex flex-col gap-3">
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Project Name *
                </label>
                <Input
                  placeholder="e.g. case-2024-001"
                  value={createForm.project_name}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, project_name: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Client
                </label>
                <Input
                  placeholder="e.g. ABC Corp"
                  value={createForm.client_name}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, client_name: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Goal
                </label>
                <Input
                  placeholder="e.g. Draft defence statement"
                  value={createForm.goal}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, goal: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Source Directory *
                </label>
                <Input
                  placeholder="/data/projects/source-docs"
                  value={createForm.dir_path}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, dir_path: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Language
                </label>
                <select
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={createForm.language}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, language: e.target.value })
                  }
                >
                  <option value="ch">Chinese (中文)</option>
                  <option value="en">English</option>
                </select>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <Button ghost onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleCreate}
                disabled={creating || !createForm.project_name.trim() || !createForm.dir_path.trim()}
              >
                {creating ? "Creating..." : "Create Project"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
