import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  FolderKanban,
  CheckSquare,
  ClipboardList,
  GitBranch,
  AlertCircle,
  Plus,
  Target,
  Calendar,
  RefreshCw,
  Paperclip,
  FileText,
} from "lucide-react";
import { api, type DashboardResponse, type Checklist, type ChecklistItem, type CpEntry, type TaskEntry } from "@/lib/api";
import { timeAgo, cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { PluginSlot } from "@/plugins";
import { PhaseTracker } from "@/components/PhaseTracker";
import { DocumentLinkPicker } from "@/components/DocumentLinkPicker";

export default function ProjectDashboardPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [dash, setDash] = useState<DashboardResponse | null>(null);
  const [checklists, setChecklists] = useState<Checklist[]>([]);
  const [cps, setCps] = useState<CpEntry[]>([]);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const [newClName, setNewClName] = useState("");
  const [newCpDesc, setNewCpDesc] = useState("");
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [linkTarget, setLinkTarget] = useState<{
    type: "checklist";
    checklistId: string;
    item: ChecklistItem;
  } | { type: "cp"; cp: CpEntry } | null>(null);

  const loadDashboard = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [d, clRes, cpRes, tRes] = await Promise.all([
        api.fetchDashboard(projectId),
        api.fetchChecklists(projectId).catch(() => ({ project_id: projectId, checklists: [] })),
        api.fetchCPs(projectId).catch(() => ({ project_id: projectId, cps: [] })),
        api.fetchTasks(projectId).catch(() => ({ project_id: projectId, tasks: [] })),
      ]);
      setDash(d);
      setChecklists(clRes.checklists ?? []);
      setCps(cpRes.cps ?? []);
      setTasks(tRes.tasks ?? []);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const handleAdvance = async () => {
    if (!projectId) return;
    setAdvancing(true);
    try {
      await api.advancePhase(projectId);
      await loadDashboard();
    } catch {
      // errors surfaced via reload
    } finally {
      setAdvancing(false);
    }
  };

  const handleCreateChecklist = async () => {
    if (!projectId || !newClName.trim()) return;
    try {
      await api.createChecklist(projectId, { name: newClName.trim() });
      setNewClName("");
      await loadDashboard();
    } catch { /* ignore */ }
  };

  const handleToggleItem = async (clId: string, itemId: string, checked: boolean) => {
    if (!projectId) return;
    try {
      await api.toggleChecklistItem(projectId, clId, itemId, !checked);
      await loadDashboard();
    } catch { /* ignore */ }
  };

  const handleCreateCp = async () => {
    if (!projectId || !newCpDesc.trim()) return;
    try {
      await api.createCP(projectId, { description: newCpDesc.trim() });
      setNewCpDesc("");
      await loadDashboard();
    } catch { /* ignore */ }
  };

  const handleCreateTask = async () => {
    if (!projectId || !newTaskTitle.trim()) return;
    try {
      await api.createTask(projectId, { title: newTaskTitle.trim() });
      setNewTaskTitle("");
      await loadDashboard();
    } catch { /* ignore */ }
  };

  const handleLinkDocs = async (refs: string[]) => {
    if (!projectId || !linkTarget) return;
    try {
      if (linkTarget.type === "checklist") {
        await api.toggleChecklistItem(
          projectId,
          linkTarget.checklistId,
          linkTarget.item.id,
          linkTarget.item.checked,
          refs,
        );
      } else {
        await api.updateCP(projectId, linkTarget.cp.id, { document_refs: refs });
      }
      setLinkTarget(null);
      await loadDashboard();
    } catch { /* ignore */ }
  };

  const handleUnlinkDoc = async (type: "checklist" | "cp", targetId: string, itemId: string | undefined, currentRefs: string[], refToRemove: string) => {
    if (!projectId) return;
    const refs = currentRefs.filter((r) => r !== refToRemove);
    try {
      if (type === "checklist" && itemId) {
        const cl = checklists.find((c) => c.items.some((it) => it.id === itemId));
        const item = cl?.items.find((it) => it.id === itemId);
        if (item) {
          await api.toggleChecklistItem(projectId, targetId, itemId, item.checked, refs);
        }
      } else {
        await api.updateCP(projectId, targetId, { document_refs: refs });
      }
      await loadDashboard();
    } catch { /* ignore */ }
  };

  if (!projectId) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-sm text-text-secondary">Invalid project ID</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-16">
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-text-secondary">{error}</p>
        <Button ghost size="sm" onClick={loadDashboard}>Retry</Button>
      </div>
    );
  }

  if (!dash) return null;

  return (
    <div className="flex min-w-0 w-full max-w-full flex-col gap-4">
      <PluginSlot name="project-dashboard:top" />

      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button ghost size="sm" onClick={() => navigate("/projects")} prefix={<ArrowLeft />}>
          Projects
        </Button>
        <span className="text-text-tertiary text-sm font-medium flex-1 truncate">
          <FolderKanban className="h-4 w-4 inline mr-1" />
          {projectId.replace(/_/g, "/")}
        </span>
        <Button ghost size="sm" onClick={loadDashboard} disabled={loading} prefix={<RefreshCw />}>
          Refresh
        </Button>
        <Button
          ghost
          size="sm"
          onClick={() => navigate(`/projects/${encodeURIComponent(projectId)}/files`)}
        >
          Browse Files
        </Button>
      </div>

      {/* Phase tracker */}
      <Card>
        <CardContent className="p-3">
          <PhaseTracker
            phases={dash.phases}
            currentIndex={dash.phase.phase_index}
            phaseHistory={dash.phase.phase_history}
            onAdvance={handleAdvance}
            advancing={advancing}
          />
        </CardContent>
      </Card>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-2xl font-bold">{dash.documents.total}</div>
            <div className="text-xs text-text-tertiary">Documents</div>
            {Object.entries(dash.documents.by_status).map(([s, n]) => (
              <Badge key={s} tone="outline" className="text-xs mt-1 mr-1">{s}: {n}</Badge>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-2xl font-bold">{dash.checklists.items_checked}/{dash.checklists.items_total}</div>
            <div className="text-xs text-text-tertiary">Checklist Items</div>
            <div className="text-xs text-text-tertiary">{dash.checklists.lists} lists</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-2xl font-bold">{dash.cps.done}/{dash.cps.total}</div>
            <div className="text-xs text-text-tertiary">CPs Met</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-2xl font-bold">{dash.tasks.done}/{dash.tasks.total}</div>
            <div className="text-xs text-text-tertiary">Tasks Done</div>
          </CardContent>
        </Card>
      </div>

      {/* Git info */}
      {dash.git.branch && (
        <Card>
          <CardContent className="p-3">
            <div className="flex items-center gap-2 text-xs text-text-secondary">
              <GitBranch className="h-3.5 w-3.5" />
              <span className="font-mono">{dash.git.branch}</span>
              <span className="font-mono text-text-tertiary">{dash.git.head}</span>
              {dash.git.dirty && <Badge tone="warning" className="text-xs">Dirty</Badge>}
            </div>
            <div className="flex flex-col gap-0.5 mt-1">
              {(dash.git.recent_commits ?? []).slice(0, 3).map((c) => (
                <div key={c.hash} className="text-xs text-text-tertiary font-mono">
                  {c.hash} {c.message}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Checklists */}
      <Card>
        <CardContent className="p-3">
          <div className="flex items-center gap-2 mb-2">
            <CheckSquare className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">Checklists</span>
          </div>
          <div className="flex gap-1 mb-2">
            <Input
              className="flex-1 h-7 text-xs"
              placeholder="New checklist name..."
              value={newClName}
              onChange={(e) => setNewClName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreateChecklist(); }}
            />
            <Button size="sm" onClick={handleCreateChecklist} disabled={!newClName.trim()} prefix={<Plus className="h-3 w-3" />}>
              Add
            </Button>
          </div>
          {checklists.length > 0 ? (
            <div className="flex flex-col gap-1">
              {checklists.map((cl: Checklist) => (
                <div key={cl.id} className="border border-border rounded p-2">
                  <div className="text-xs font-medium mb-1">{cl.name}</div>
                  {cl.items.map((it) => (
                    <div key={it.id} className="flex items-center gap-2 py-0.5 text-xs">
                      <label className="flex items-center gap-2 cursor-pointer flex-1 min-w-0">
                        <input
                          type="checkbox"
                          checked={it.checked}
                          onChange={() => handleToggleItem(cl.id, it.id, it.checked)}
                          className="rounded"
                        />
                        <span className={cn(it.checked && "line-through text-text-tertiary", "truncate")}>{it.text}</span>
                      </label>
                      {(it.document_refs ?? []).length > 0 && (
                        <span className="flex items-center gap-0.5 shrink-0">
                          {(it.document_refs ?? []).map((ref) => (
                            <Badge
                              key={ref}
                              tone="outline"
                              className="text-[10px] cursor-pointer hover:bg-secondary/60 flex items-center gap-0.5"
                              title={ref}
                              onClick={() => handleUnlinkDoc("checklist", cl.id, it.id, it.document_refs ?? [], ref)}
                            >
                              <FileText className="h-2 w-2" />
                              {ref.split("/").pop()}
                            </Badge>
                          ))}
                        </span>
                      )}
                      <Button
                        ghost
                        size="sm"
                        className="h-5 w-5 shrink-0"
                        onClick={() => setLinkTarget({ type: "checklist", checklistId: cl.id, item: it })}
                        title="Link document"
                      >
                        <Paperclip className="h-2.5 w-2.5 text-text-tertiary hover:text-primary" />
                      </Button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-text-tertiary">No checklists yet.</p>
          )}
        </CardContent>
      </Card>

      {/* CPs + Tasks row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* CPs */}
        <Card>
          <CardContent className="p-3">
            <div className="flex items-center gap-2 mb-2">
              <Target className="h-4 w-4 text-warning" />
              <span className="text-sm font-medium">Conditions Precedent</span>
            </div>
            <div className="flex gap-1 mb-2">
              <Input
                className="flex-1 h-7 text-xs"
                placeholder="New CP description..."
                value={newCpDesc}
                onChange={(e) => setNewCpDesc(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleCreateCp(); }}
              />
              <Button size="sm" onClick={handleCreateCp} disabled={!newCpDesc.trim()} prefix={<Plus className="h-3 w-3" />}>
                Add
              </Button>
            </div>
            {cps?.length > 0 ? (
              <div className="flex flex-col gap-1">
                {cps?.map((cp: CpEntry) => (
                  <div key={cp.id} className="flex items-center gap-2 text-xs py-1">
                    <Badge tone={cp.status === "met" ? "success" : cp.status === "waived" ? "outline" : "warning"} className="text-xs">
                      {cp.status}
                    </Badge>
                    <span className="flex-1 truncate">{cp.description}</span>
                    {(cp.document_refs ?? []).length > 0 && (
                      <span className="flex items-center gap-0.5 shrink-0">
                        {(cp.document_refs ?? []).map((ref) => (
                          <Badge
                            key={ref}
                            tone="outline"
                            className="text-[10px] cursor-pointer hover:bg-secondary/60 flex items-center gap-0.5"
                            title={ref}
                            onClick={() => handleUnlinkDoc("cp", cp.id, undefined, cp.document_refs ?? [], ref)}
                          >
                            <FileText className="h-2 w-2" />
                            {ref.split("/").pop()}
                          </Badge>
                        ))}
                      </span>
                    )}
                    <Button
                      ghost
                      size="sm"
                      className="h-5 w-5 shrink-0"
                      onClick={() => setLinkTarget({ type: "cp", cp })}
                      title="Link document"
                    >
                      <Paperclip className="h-2.5 w-2.5 text-text-tertiary hover:text-primary" />
                    </Button>
                    {cp.due_date && <Calendar className="h-3 w-3 text-text-tertiary" />}
                    {cp.due_date && <span className="text-text-tertiary">{cp.due_date}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-text-tertiary">No CPs yet.</p>
            )}
          </CardContent>
        </Card>

        {/* Tasks */}
        <Card>
          <CardContent className="p-3">
            <div className="flex items-center gap-2 mb-2">
              <ClipboardList className="h-4 w-4 text-info" />
              <span className="text-sm font-medium">Tasks</span>
            </div>
            <div className="flex gap-1 mb-2">
              <Input
                className="flex-1 h-7 text-xs"
                placeholder="New task title..."
                value={newTaskTitle}
                onChange={(e) => setNewTaskTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleCreateTask(); }}
              />
              <Button size="sm" onClick={handleCreateTask} disabled={!newTaskTitle.trim()} prefix={<Plus className="h-3 w-3" />}>
                Add
              </Button>
            </div>
            {tasks?.length > 0 ? (
              <div className="flex flex-col gap-1">
                {tasks?.map((t: TaskEntry) => (
                  <div key={t.id} className="flex items-center gap-2 text-xs py-1">
                    <Badge
                      tone={t.status === "done" ? "success" : t.status === "in_progress" ? "warning" : "outline"}
                      className="text-xs"
                    >
                      {t.status}
                    </Badge>
                    <span className="flex-1 truncate">{t.title}</span>
                    {t.assignee && <span className="text-text-tertiary">{t.assignee}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-text-tertiary">No tasks yet.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Document Link Picker */}
      {linkTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/30" onClick={() => setLinkTarget(null)} />
          <div className="relative z-10">
            <DocumentLinkPicker
              projectId={projectId}
              selected={
                linkTarget.type === "checklist"
                  ? linkTarget.item.document_refs ?? []
                  : linkTarget.cp.document_refs ?? []
              }
              onApply={handleLinkDocs}
              onClose={() => setLinkTarget(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
