import {
  useEffect,
  useState,
  useCallback,
  useRef,
} from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  FolderKanban,
  FileText,
  File,
  Upload,
  Trash2,
  RefreshCw,
  FolderOpen,
  ChevronRight,
  Home,
  AlertCircle,
  Download,
  History,
  Search,
  FileSearch,
  FilePenLine,
  Eye,
  ArrowLeftRight,
  BookOpen,
} from "lucide-react";
import { api, type FileEntry, type DocumentMeta } from "@/lib/api";
import { formatFileSize, cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { PluginSlot } from "@/plugins";
import { VersionHistory } from "@/components/VersionHistory";
import { FileDetailPanel } from "@/components/FileDetailPanel";
import { DiffViewer } from "@/components/DiffViewer";
import { BinderDialog } from "@/components/BinderDialog";

export default function ProjectFilesPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [files, setFiles] = useState<FileEntry[]>([]);
  const [currentPath, setCurrentPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Array<{ path: string; name: string; matches: string[]; size: number }>>([]);
  const [inventory, setInventory] = useState<Record<string, DocumentMeta>>({});
  const [detailPath, setDetailPath] = useState<string | null>(null);
  const [detailName, setDetailName] = useState("");
  const [compareMode, setCompareMode] = useState(false);
  const [compareA, setCompareA] = useState<string | null>(null);
  const [compareB, setCompareB] = useState<string | null>(null);
  const [binderOpen, setBinderOpen] = useState(false);
  const [selectedForBinder, setSelectedForBinder] = useState<string[]>([]);
  const [searchFilters, setSearchFilters] = useState<{
    status: string;
    file_type: string;
  }>({ status: "", file_type: "" });

  const { toast, showToast } = useToast();

  const fileDelete = useConfirmDelete({
    onDelete: useCallback(
      async (path: string) => {
        if (!projectId) return;
        try {
          await api.deleteProjectFile(projectId, path);
          await loadFiles();
        } catch (e: any) {
          showToast(e?.message ?? "Delete failed", "error");
          throw new Error("delete failed");
        }
      },
      [projectId, showToast],
    ),
  });

  const loadFiles = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [data, invRes] = await Promise.all([
        api.fetchProjectFiles(projectId, currentPath || undefined),
        api.fetchInventory(projectId).catch(() => null),
      ]);
      setFiles(data.files ?? []);
      setProjectName(data.project_name ?? "");
      if (invRes?.inventory?.documents) {
        setInventory(invRes.inventory.documents);
      }
    } catch (e: any) {
      setError(e?.message ?? "Failed to load files");
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [projectId, currentPath]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const navigateTo = (relPath: string) => {
    setCurrentPath(relPath);
  };

  const navigateUp = () => {
    if (!currentPath) return;
    const parts = currentPath.split("/");
    parts.pop();
    setCurrentPath(parts.join("/"));
  };

  const handleUpload = async (fileList: FileList | File[]) => {
    if (!projectId || fileList.length === 0) return;
    setUploading(true);
    try {
      const result = await api.uploadProjectFiles(
        projectId,
        Array.from(fileList),
        currentPath || undefined,
      );
      if (result.uploaded.length > 0) {
        showToast(`Uploaded ${result.uploaded.length} file${result.uploaded.length !== 1 ? "s" : ""}`, "success");
      }
      if (result.errors.length > 0) {
        for (const err of result.errors) {
          showToast(`${err.filename}: ${err.error}`, "error");
        }
      }
      await loadFiles();
    } catch (e: any) {
      showToast(e?.message ?? "Upload failed", "error");
    } finally {
      setUploading(false);
    }
  };

  const handleSearch = async () => {
    if (!projectId || !searchQuery.trim()) return;
    try {
      const res = await api.searchDocuments(projectId, searchQuery.trim(), 30, {
        status: searchFilters.status || undefined,
        file_type: searchFilters.file_type || undefined,
      });
      setSearchResults(res.results ?? []);
    } catch {
      // errors surfaced inline
    }
  };

  const getDocIcon = (name: string, isDir: boolean) => {
    if (isDir) return <FolderOpen className="h-5 w-5 text-warning flex-shrink-0" />;
    const ext = name.split(".").pop()?.toLowerCase();
    switch (ext) {
      case "docx":
        return <FileText className="h-5 w-5 text-[#2B7CD3] flex-shrink-0" />;
      case "pdf":
        return <FileText className="h-5 w-5 text-[#DC2626] flex-shrink-0" />;
      case "jpg": case "jpeg": case "png": case "gif": case "svg": case "webp":
        return <FileText className="h-5 w-5 text-[#8B5CF6] flex-shrink-0" />;
      case "xlsx": case "xls":
        return <FileText className="h-5 w-5 text-[#059669] flex-shrink-0" />;
      default:
        return <File className="h-5 w-5 text-text-tertiary flex-shrink-0" />;
    }
  };

  const getDocStatusBadge = (filePath: string) => {
    const meta = inventory[filePath];
    if (!meta?.status && !meta?.signing_status) return null;
    const tones: Record<string, { tone: "outline" | "warning" | "success"; label: string }> = {
      draft: { tone: "outline", label: "Draft" },
      review: { tone: "warning", label: "In Review" },
      final: { tone: "success", label: "Final" },
      signed: { tone: "success", label: "Signed" },
      archived: { tone: "outline", label: "Archived" },
    };
    const t = meta.status ? (tones[meta.status] ?? { tone: "outline" as const, label: meta.status }) : null;
    const signing = meta.signing_status && meta.signing_status !== "unsigned" ? meta.signing_status : null;
    return (
      <span className="flex items-center gap-1 flex-shrink-0">
        {t && <Badge tone={t.tone} className="text-xs">{t.label}</Badge>}
        {signing && <Badge tone={signing === "signed" ? "success" : signing === "partially-signed" ? "warning" : "outline"} className="text-[10px]">{signing}</Badge>}
      </span>
    );
  };

  const handleDownload = (file: FileEntry) => {
    if (!projectId) return;
    api.downloadProjectFile(projectId, file.path);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files);
    }
  };

  const breadcrumbs = currentPath
    ? currentPath.split("/").filter(Boolean)
    : [];

  if (!projectId) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-sm text-text-secondary">Invalid project ID</div>
      </div>
    );
  }

  return (
    <div
      className="flex min-w-0 w-full max-w-full flex-col gap-4"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <PluginSlot name="project-files:top" />
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={fileDelete.isOpen}
        onCancel={fileDelete.cancel}
        onConfirm={fileDelete.confirm}
        title="Delete File"
        description="This will permanently delete the file. This action cannot be undone."
        loading={fileDelete.isDeleting}
      />

      {/* Back + breadcrumb bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          ghost
          size="sm"
          onClick={() => navigate("/projects")}
          prefix={<ArrowLeft />}
        >
          Projects
        </Button>

        <span className="text-text-tertiary">/</span>

        <Button
          ghost
          size="sm"
          onClick={() => setCurrentPath("")}
          className={cn(!currentPath && "text-primary font-medium")}
          prefix={!currentPath ? <FolderOpen className="h-3.5 w-3.5" /> : <Home className="h-3.5 w-3.5" />}
        >
          {projectName || "Files"}
        </Button>

        {breadcrumbs.map((name, i) => {
          const path = breadcrumbs.slice(0, i + 1).join("/");
          const isLast = i === breadcrumbs.length - 1;
          return (
            <span key={path} className="flex items-center gap-3">
              <ChevronRight className="h-3.5 w-3.5 text-text-tertiary" />
              <Button
                ghost
                size="sm"
                onClick={() => setCurrentPath(path)}
                className={cn(isLast && "text-primary font-medium")}
              >
                {name}
              </Button>
            </span>
          );
        })}

        <div className="flex-1" />

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleUpload(e.target.files);
            e.target.value = "";
          }}
        />

        <Button
          ghost
          size="sm"
          onClick={loadFiles}
          disabled={loading}
          prefix={<RefreshCw className={cn(loading && "animate-spin")} />}
        >
          Refresh
        </Button>

        <Button
          size="sm"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          prefix={uploading ? <Spinner /> : <Upload />}
        >
          {uploading ? "Uploading..." : "Upload"}
        </Button>

        <Button
          ghost
          size="sm"
          onClick={() => setVersionOpen((v) => !v)}
          title="Version history"
          prefix={<History className={cn(versionOpen && "text-primary")} />}
        >
          History
        </Button>

        <Button
          ghost
          size="sm"
          onClick={() => { setCompareMode((v) => !v); setCompareA(null); setCompareB(null); }}
          title="Compare two files"
          prefix={<ArrowLeftRight className={cn(compareMode && "text-primary")} />}
        >
          Compare
        </Button>

        <Button
          ghost
          size="sm"
          onClick={() => {
            setBinderOpen(true);
          }}
          title="Create a binder from selected files"
          disabled={selectedForBinder.length < 2}
          prefix={<BookOpen className="h-3.5 w-3.5" />}
        >
          Binder {selectedForBinder.length > 0 ? `(${selectedForBinder.length})` : ""}
        </Button>

        <div className="relative flex items-center gap-2">
          <div className="relative w-48">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-tertiary" />
            <Input
              className="pl-7 h-8 text-xs"
              placeholder="Search documents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSearch(); }}
            />
          </div>
          <select
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-text-secondary"
            value={searchFilters.status}
            onChange={(e) => setSearchFilters((f) => ({ ...f, status: e.target.value }))}
          >
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="review">In Review</option>
            <option value="final">Final</option>
            <option value="signed">Signed</option>
            <option value="archived">Archived</option>
          </select>
          <select
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-text-secondary"
            value={searchFilters.file_type}
            onChange={(e) => setSearchFilters((f) => ({ ...f, file_type: e.target.value }))}
          >
            <option value="">All types</option>
            <option value="docx">DOCX</option>
            <option value="pdf">PDF</option>
            <option value="xlsx">XLSX</option>
          </select>
        </div>
      </div>

      {/* Version history panel */}
      {versionOpen && (
        <Card className="p-3">
          <VersionHistory projectId={projectId} />
        </Card>
      )}

      {error && (
        <div className="flex items-start gap-2 border border-destructive/30 bg-destructive/[0.06] p-3 text-sm">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-destructive" />
          <div className="min-w-0 flex-1">
            <div className="text-destructive">{error}</div>
            <Button ghost size="sm" className="mt-1" onClick={loadFiles}>
              Retry
            </Button>
          </div>
        </div>
      )}

      {/* Search results */}
      {searchResults.length > 0 && (
        <Card>
          <CardContent className="p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5 text-xs text-text-secondary">
                <FileSearch className="h-3.5 w-3.5" />
                <span>{searchResults.length} result{searchResults.length !== 1 ? "s" : ""} for "{searchQuery}"</span>
              </div>
              <Button ghost size="sm" onClick={() => { setSearchResults([]); setSearchQuery(""); }}>
                Clear
              </Button>
            </div>
            <div className="flex flex-col gap-1">
              {searchResults.map((r) => (
                <button
                  key={r.path}
                  type="button"
                  className="flex items-start gap-3 px-2 py-1.5 rounded text-left hover:bg-muted/5 text-xs"
                  onClick={() => {
                    setDetailPath(r.path);
                    setDetailName(r.name);
                    loadFiles();
                  }}
                >
                  {getDocIcon(r.name, false)}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{r.path}</div>
                    {r.matches.slice(0, 2).map((m, i) => (
                      <div key={i} className="text-text-tertiary truncate mt-0.5">...{m}...</div>
                    ))}
                  </div>
                  <Badge tone="outline" className="text-xs flex-shrink-0">
                    {formatFileSize(r.size)}
                  </Badge>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Drop zone overlay */}
      {dragOver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm pointer-events-none">
          <Card className="border-2 border-dashed border-primary p-8 shadow-xl">
            <div className="flex flex-col items-center gap-2">
              <Upload className="h-10 w-10 text-primary" />
              <p className="text-sm font-medium">Drop files to upload</p>
            </div>
          </Card>
        </div>
      )}

      {/* Main content: file list + optional detail panel */}
      <div className="flex-1 min-h-0 flex gap-0">
        {/* File listing */}
        <div className={cn("flex-1 min-w-0 flex flex-col gap-1", detailPath && "pr-1")}>
          {loading ? (
            <div className="flex items-center justify-center py-24">
              <Spinner className="text-2xl text-primary" />
            </div>
          ) : files.length === 0 ? (
            <Card>
              <CardContent className="py-16 flex flex-col items-center gap-3">
                <FolderKanban className="h-12 w-12 text-text-tertiary" />
                <p className="text-text-secondary text-sm">
                  {currentPath
                    ? "This directory is empty."
                    : "No files in this project yet."}
                </p>
                <Button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  size="sm"
                >
                  <Upload className="h-4 w-4 mr-1" />
                  Upload Files
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Directory up / subdirectories */}
              {currentPath && (
                <button
                  type="button"
                  onClick={navigateUp}
                  className="flex items-center gap-3 px-3 py-2 rounded text-sm hover:bg-muted/10 transition-colors text-left w-full"
                >
                  <FolderOpen className="h-4 w-4 text-text-tertiary" />
                  <span className="text-text-secondary">..</span>
                </button>
              )}

              {files.map((f) => (
                <div
                  key={f.path}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2 rounded group transition-colors",
                    f.is_dir
                      ? "cursor-pointer hover:bg-muted/10"
                      : "cursor-pointer hover:bg-muted/5",
                    !f.is_dir && detailPath === f.path && "bg-primary/10 border-l-2 border-l-primary",
                  )}
                  onClick={() => {
                    if (f.is_dir) {
                      navigateTo(f.path);
                    } else if (compareMode) {
                      if (!compareA) setCompareA(f.path);
                      else if (!compareB && f.path !== compareA) setCompareB(f.path);
                    } else {
                      setDetailPath(f.path);
                      setDetailName(f.name);
                    }
                  }}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  if (f.is_dir) navigateTo(f.path);
                  else {
                    setDetailPath(f.path);
                    setDetailName(f.name);
                  }
                }
              }}
            >
              {!f.is_dir && (
                <input
                  type="checkbox"
                  className="rounded shrink-0 opacity-50 group-hover:opacity-100 transition-opacity"
                  checked={selectedForBinder.includes(f.path)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => {
                    setSelectedForBinder((prev) =>
                      prev.includes(f.path)
                        ? prev.filter((p) => p !== f.path)
                        : [...prev, f.path],
                    );
                  }}
                />
              )}
              {getDocIcon(f.name, f.is_dir)}

              <span
                className={cn(
                  "flex-1 min-w-0 truncate text-sm",
                  f.is_dir ? "font-medium" : "",
                )}
              >
                {f.name}
              </span>

              {!f.is_dir && getDocStatusBadge(f.path)}

              {!f.is_dir && (
                <Badge tone="outline" className="text-xs flex-shrink-0 hidden sm:inline-flex">
                  {formatFileSize(f.size)}
                </Badge>
              )}

              {!f.is_dir && (
                <span className="text-xs text-text-tertiary hidden md:inline-block w-30 text-right flex-shrink-0">
                  {f.modified}
                </span>
              )}

              <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                {!f.is_dir && (
                  <>
                    <Button
                      ghost
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDetailPath(f.path);
                        setDetailName(f.name);
                      }}
                      title="Preview"
                    >
                      <Eye className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      ghost
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDetailPath(f.path);
                        setDetailName(f.name);
                      }}
                      title="Edit metadata"
                    >
                      <FilePenLine className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      ghost
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownload(f);
                      }}
                      title="Download"
                    >
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                  </>
                )}
                <Button
                  ghost
                  size="icon"
                  className="h-7 w-7"
                  onClick={(e) => {
                    e.stopPropagation();
                    fileDelete.requestDelete(f.path);
                  }}
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5 text-text-tertiary hover:text-destructive" />
                </Button>
              </div>
            </div>
          ))}

              <p className="text-xs text-text-tertiary px-3 mt-2">
                {files.length} item{files.length !== 1 ? "s" : ""}
              </p>
            </>
          )}
        </div>

        {/* Right detail panel */}
        {detailPath && (
          <div className="w-[420px] shrink-0">
            <div className="sticky top-0 h-[calc(100vh-16rem)]">
              <FileDetailPanel
                projectId={projectId}
                filePath={detailPath}
                fileName={detailName}
                meta={inventory[detailPath]}
                onClose={() => { setDetailPath(null); setDetailName(""); }}
                onMetaSaved={(meta) => {
                  setInventory((prev) => ({ ...prev, [detailPath!]: meta }));
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Diff viewer modal */}
      {compareA && compareB && (
        <DiffViewer
          projectId={projectId}
          original={compareA}
          revised={compareB}
          onClose={() => { setCompareA(null); setCompareB(null); }}
        />
      )}

      {compareMode && !(compareA && compareB) && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 bg-card border border-border shadow-lg rounded-lg px-4 py-2 text-xs flex items-center gap-2">
          <ArrowLeftRight className="h-3.5 w-3.5 text-primary" />
          <span>
            {!compareA
              ? "Select first file to compare"
              : "Select second file to compare"}
          </span>
          <span className="text-text-tertiary">
            ({compareA ? compareA.split("/").pop() : "?"} vs {compareB ? compareB.split("/").pop() : "?"})
          </span>
          <Button ghost size="sm" className="text-xs h-6" onClick={() => { setCompareMode(false); setCompareA(null); setCompareB(null); }}>
            Cancel
          </Button>
        </div>
      )}

      {/* Binder dialog */}
      {binderOpen && (
        <BinderDialog
          projectId={projectId}
          files={selectedForBinder}
          onClose={() => setBinderOpen(false)}
        />
      )}
    </div>
  );
}
