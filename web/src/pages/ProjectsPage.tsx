import { useEffect, useState, useCallback } from "react";
import {
  FolderKanban,
  Plus,
  Search,
  Trash2,
  FileText,
  Calendar,
  Building2,
  Target,
  ChevronRight,
  X,
} from "lucide-react";
import { api, type ProjectInfo } from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { PluginSlot } from "@/plugins";

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

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { toast, showToast } = useToast();
  const projectDelete = useConfirmDelete({
    onDelete: useCallback(
      async (id: string) => {
        try {
          await api.deleteProject(id);
          setProjects((prev) => prev.filter((p) => p.id !== id));
          showToast("Project deleted", "success");
        } catch (e: any) {
          showToast(e?.message ?? "Delete failed", "error");
          throw new Error("delete failed");
        }
      },
      [showToast],
    ),
  });

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchProjects();
      setProjects(data.projects ?? []);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load projects");
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  const handleCreate = async () => {
    if (!form.project_name.trim() || !form.dir_path.trim()) return;
    setCreating(true);
    try {
      await api.createProject({
        project_name: form.project_name.trim(),
        client_name: form.client_name.trim(),
        goal: form.goal.trim(),
        dir_path: form.dir_path.trim(),
        language: form.language,
      });
      setShowCreate(false);
      setForm(EMPTY_FORM);
      showToast("Project created", "success");
      await loadProjects();
    } catch (e: any) {
      showToast(e?.message ?? "Create failed", "error");
    } finally {
      setCreating(false);
    }
  };

  const filtered = search.trim()
    ? projects.filter(
        (p) =>
          p.name.toLowerCase().includes(search.toLowerCase()) ||
          (p.client ?? "").toLowerCase().includes(search.toLowerCase()),
      )
    : projects;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 w-full max-w-full flex-col gap-4">
      <PluginSlot name="projects:top" />
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={projectDelete.isOpen}
        onCancel={projectDelete.cancel}
        onConfirm={projectDelete.confirm}
        title="Delete Project"
        description="This will permanently delete the project directory and all its contents. This action cannot be undone."
        loading={projectDelete.isDeleting}
      />

      {error && (
        <div className="border border-destructive/30 bg-destructive/[0.06] p-4 text-sm">
          {error}
          <Button ghost size="sm" className="ml-4" onClick={loadProjects}>
            Retry
          </Button>
        </div>
      )}

      {/* Search + Create bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-tertiary" />
          <Input
            className="pl-9"
            placeholder="Search projects by name or client..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button
              className="absolute right-3 top-1/2 -translate-y-1/2"
              onClick={() => setSearch("")}
              aria-label="Clear search"
            >
              <X className="h-4 w-4 text-text-tertiary" />
            </button>
          )}
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-1" />
          New Project
        </Button>
      </div>

      {/* Project list */}
      {filtered.length === 0 ? (
        <Card>
          <CardContent className="py-12 flex flex-col items-center gap-3">
            <FolderKanban className="h-12 w-12 text-text-tertiary" />
            <p className="text-text-secondary text-sm">
              {search.trim()
                ? "No projects match your search."
                : "No projects yet. Create your first legal project."}
            </p>
            {!search.trim() && (
              <Button onClick={() => setShowCreate(true)}>
                <Plus className="h-4 w-4 mr-1" />
                Create Project
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((proj) => (
            <Card key={proj.id} className="hover:bg-card-hover transition-colors">
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <FolderKanban className="h-5 w-5 text-primary flex-shrink-0" />
                      <h3 className="font-semibold text-base truncate">
                        {proj.name}
                      </h3>
                    </div>

                    <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-text-secondary">
                      {proj.client && (
                        <span className="flex items-center gap-1">
                          <Building2 className="h-3 w-3" />
                          {proj.client}
                        </span>
                      )}
                      {proj.goal && (
                        <span className="flex items-center gap-1 truncate max-w-[400px]">
                          <Target className="h-3 w-3 flex-shrink-0" />
                          <span className="truncate">{proj.goal}</span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-3 mt-2 text-xs text-text-tertiary">
                      {proj.created && (
                        <span className="flex items-center gap-1">
                          <Calendar className="h-3 w-3" />
                          {timeAgo(proj.created)}
                        </span>
                      )}
                      <span className="flex items-center gap-1">
                        <FileText className="h-3 w-3" />
                        {proj.doc_count ?? 0} docs
                      </span>
                      <Badge tone="outline" className="text-xs">
                        {proj.directory}
                      </Badge>
                    </div>
                  </div>

                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      ghost
                      size="sm"
                      onClick={() => projectDelete.requestDelete(proj.id)}
                      aria-label={`Delete ${proj.name}`}
                    >
                      <Trash2 className="h-4 w-4 text-text-tertiary hover:text-destructive" />
                    </Button>
                    <ChevronRight className="h-5 w-5 text-text-tertiary" />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
          <p className="text-xs text-text-tertiary px-1">
            {filtered.length} project{filtered.length !== 1 ? "s" : ""}
          </p>
        </div>
      )}

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
              <Button
                ghost
                size="sm"
                onClick={() => setShowCreate(false)}
              >
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
                  value={form.project_name}
                  onChange={(e) =>
                    setForm({ ...form, project_name: e.target.value })
                  }
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Client
                </label>
                <Input
                  placeholder="e.g. ABC Corp"
                  value={form.client_name}
                  onChange={(e) =>
                    setForm({ ...form, client_name: e.target.value })
                  }
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Goal
                </label>
                <Input
                  placeholder="e.g. Draft defence statement"
                  value={form.goal}
                  onChange={(e) =>
                    setForm({ ...form, goal: e.target.value })
                  }
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Source Directory *
                </label>
                <Input
                  placeholder="/data/projects/source-docs"
                  value={form.dir_path}
                  onChange={(e) =>
                    setForm({ ...form, dir_path: e.target.value })
                  }
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Language
                </label>
                <select
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={form.language}
                  onChange={(e) =>
                    setForm({ ...form, language: e.target.value })
                  }
                >
                  <option value="ch">Chinese (中文)</option>
                  <option value="en">English</option>
                </select>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <Button
                ghost
                onClick={() => setShowCreate(false)}
              >
                Cancel
              </Button>
              <Button
                onClick={handleCreate}
                disabled={creating || !form.project_name.trim() || !form.dir_path.trim()}
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
