import {
  useEffect,
  useState,
  useCallback,
  useRef,
} from "react";
import { useParams } from "react-router-dom";
import {
  FolderKanban,
  Upload,
  Trash2,
  RefreshCw,
  AlertCircle,
  Download,
  History,
  Search,
  FileSearch,
  FilePenLine,
  Eye,
  Copy,
  ArrowLeftRight,
  BookOpen,
} from "lucide-react";
import { api, type FileEntry, type DocumentMeta } from "@/lib/api";
import { formatFileSize, cn } from "@/lib/utils";
import { loadPanelSize } from "@/lib/layout-persistence";
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "@/components/ResizablePanel";
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
import { TreeView } from "@/components/TreeView";
import { getDocIcon, getDocStatusBadge } from "@/lib/file-utils";
import { usePageHeader } from "@/contexts/usePageHeader";
import { ContextMenu } from "@/components/ContextMenu";
import { ContextMenuItem, ContextMenuSeparator } from "@/components/ContextMenuItem";
import type { FileTreeNode } from "@/lib/file-utils";

export default function ProjectFilesPage() {
  const { projectId } = useParams<{ projectId: string }>();
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
  const [ctxMenuPos, setCtxMenuPos] = useState<{ x: number; y: number } | null>(null);
  const [ctxMenuNode, setCtxMenuNode] = useState<FileTreeNode | null>(null);

  const { toast, showToast } = useToast();
  const { setBreadcrumb, setToolbarActions } = usePageHeader();

  const breadcrumbs = currentPath
    ? currentPath.split("/").filter(Boolean)
    : [];

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

  // Sync breadcrumb to ViewToolbar
  useEffect(() => {
    const segments: { label: string; href?: string }[] = [
      { label: projectName || "Files", href: `/projects/${projectId}/files` },
    ];
    for (let i = 0; i < breadcrumbs.length; i++) {
      const subpath = breadcrumbs.slice(0, i + 1).join("/");
      segments.push({ label: breadcrumbs[i], href: `/projects/${projectId}/files/${subpath}` });
    }
    setBreadcrumb(segments);
  }, [projectName, projectId, breadcrumbs, setBreadcrumb]);

  // Sync toolbar actions to ViewToolbar
  useEffect(() => {
    setToolbarActions(
      <div className="flex items-center gap-1">
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
          prefix={<History className={cn(versionOpen && "text-primary")} />}
        >
          History
        </Button>
        <Button
          ghost
          size="sm"
          onClick={() => { setCompareMode((v) => !v); setCompareA(null); setCompareB(null); }}
          prefix={<ArrowLeftRight className={cn(compareMode && "text-primary")} />}
        >
          Compare
        </Button>
        <Button
          ghost
          size="sm"
          onClick={() => setBinderOpen(true)}
          disabled={selectedForBinder.length < 2}
          prefix={<BookOpen className="h-3.5 w-3.5" />}
        >
          Binder {selectedForBinder.length > 0 ? `(${selectedForBinder.length})` : ""}
        </Button>
        <div className="flex items-center gap-1.5 ml-2">
          <div className="relative w-40">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-tertiary" />
            <Input
              className="pl-7 h-7 text-xs"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSearch(); }}
            />
          </div>
          <select
            className="h-7 rounded border border-border bg-background px-2 text-[11px] text-text-secondary"
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
            className="h-7 rounded border border-border bg-background px-2 text-[11px] text-text-secondary"
            value={searchFilters.file_type}
            onChange={(e) => setSearchFilters((f) => ({ ...f, file_type: e.target.value }))}
          >
            <option value="">All types</option>
            <option value="docx">DOCX</option>
            <option value="pdf">PDF</option>
            <option value="xlsx">XLSX</option>
          </select>
        </div>
      </div>,
    );
  }, [
    setToolbarActions, loading, uploading, versionOpen, compareMode, selectedForBinder.length,
    searchQuery, searchFilters, loadFiles,
  ]);

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

  const handleDownload = async (file: { path: string }) => {
    if (!projectId) return;
    try {
      await api.downloadProjectFile(projectId, file.path);
    } catch (e: any) {
      showToast(e?.message ?? "Download failed", "error");
    }
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

      {/* Hidden file input (triggered from toolbar) */}
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
      <ResizablePanelGroup orientation="horizontal" className="flex-1">
        {/* File listing */}
        <ResizablePanel
          defaultSize={detailPath ? loadPanelSize("files-list") : 100}
          minSize={40}
        >
        <div className="hermes-desktop-pane flex-1 min-w-0 flex flex-col gap-1 overflow-hidden">
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
            <TreeView
              files={files}
              currentPath={currentPath}
              selectedPath={detailPath}
              onSelectPath={(path, name) => {
                if (compareMode) {
                  if (!compareA) setCompareA(path);
                  else if (!compareB && path !== compareA) setCompareB(path);
                } else {
                  setDetailPath(path);
                  setDetailName(name);
                }
              }}
              onNavigate={(path) => setCurrentPath(path)}
              renderIcon={(name, isDir) => getDocIcon(name, isDir)}
              renderBadge={(path) => getDocStatusBadge(inventory, path)}
              renderSize={(node) => (
                <Badge tone="outline" className="text-[10px] flex-shrink-0 hidden sm:inline-flex">
                  {formatFileSize(node.size)}
                </Badge>
              )}
              renderCheckbox={(node) =>
                !node.isDir ? (
                  <input
                    type="checkbox"
                    className="rounded shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
                    checked={selectedForBinder.includes(node.path)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      e.stopPropagation();
                      setSelectedForBinder((prev) =>
                        prev.includes(node.path)
                          ? prev.filter((p) => p !== node.path)
                          : [...prev, node.path],
                      );
                    }}
                  />
                ) : null
              }
              renderActions={(node) => (
                <>
                  {!node.isDir && (
                    <>
                      <Button
                        ghost
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDetailPath(node.path);
                          setDetailName(node.name);
                        }}
                        title="Preview"
                      >
                        <Eye className="h-3 w-3" />
                      </Button>
                      <Button
                        ghost
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDetailPath(node.path);
                          setDetailName(node.name);
                        }}
                        title="Edit metadata"
                      >
                        <FilePenLine className="h-3 w-3" />
                      </Button>
                      <Button
                        ghost
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDownload(node);
                        }}
                        title="Download"
                      >
                        <Download className="h-3 w-3" />
                      </Button>
                    </>
                  )}
                  <Button
                    ghost
                    size="icon"
                    className="h-6 w-6"
                    onClick={(e) => {
                      e.stopPropagation();
                      fileDelete.requestDelete(node.path);
                    }}
                    title="Delete"
                  >
                    <Trash2 className="h-3 w-3 text-text-tertiary hover:text-destructive" />
                  </Button>
                </>
              )}
              onContextMenu={(e, node) => {
                e.preventDefault();
                setCtxMenuPos({ x: e.clientX, y: e.clientY });
                setCtxMenuNode(node);
              }}
              searchQuery={searchQuery}
            />
          )}
        </div>
        </ResizablePanel>

        {/* Right detail panel */}
        {detailPath && (
          <>
            <ResizableHandle className="mx-0" />
            <ResizablePanel
              defaultSize={loadPanelSize("files-detail")}
              minSize={20}
              maxSize={50}
            >
            <div className="hermes-desktop-pane sticky top-0 h-[calc(100vh-16rem)] overflow-hidden">
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
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>

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

      {/* File context menu */}
      {ctxMenuPos && ctxMenuNode && (
        <ContextMenu
          position={ctxMenuPos}
          onClose={() => { setCtxMenuPos(null); setCtxMenuNode(null); }}
        >
          <ContextMenuItem
            icon={<Eye className="h-3.5 w-3.5" />}
            label="Preview"
            onClick={() => {
              setDetailPath(ctxMenuNode.path);
              setDetailName(ctxMenuNode.name);
              setCtxMenuPos(null);
              setCtxMenuNode(null);
            }}
          />
          <ContextMenuItem
            icon={<FilePenLine className="h-3.5 w-3.5" />}
            label="Edit Metadata"
            onClick={() => {
              setDetailPath(ctxMenuNode.path);
              setDetailName(ctxMenuNode.name);
              setCtxMenuPos(null);
              setCtxMenuNode(null);
            }}
          />
          <ContextMenuItem
            icon={<Download className="h-3.5 w-3.5" />}
            label="Download"
            onClick={() => {
              handleDownload(ctxMenuNode);
              setCtxMenuPos(null);
              setCtxMenuNode(null);
            }}
          />
          <ContextMenuItem
            icon={<Copy className="h-3.5 w-3.5" />}
            label="Copy Path"
            onClick={() => {
              navigator.clipboard.writeText(ctxMenuNode.path).catch(() => {});
              setCtxMenuPos(null);
              setCtxMenuNode(null);
            }}
          />
          <ContextMenuSeparator />
          <ContextMenuItem
            variant="destructive"
            icon={<Trash2 className="h-3.5 w-3.5" />}
            label="Delete"
            onClick={() => {
              fileDelete.requestDelete(ctxMenuNode.path);
              setCtxMenuPos(null);
              setCtxMenuNode(null);
            }}
          />
        </ContextMenu>
      )}
    </div>
  );
}
