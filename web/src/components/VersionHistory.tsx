import {
  useEffect,
  useState,
  useCallback,
} from "react";
import {
  GitBranch,
  GitCommit,
  Plus,
  RefreshCw,
  Clock,
  Circle,
  AlertTriangle,
} from "lucide-react";
import { api, type CommitEntry, type BranchInfo, type WorktreeInfo } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn } from "@/lib/utils";

interface VersionHistoryProps {
  projectId: string;
  className?: string;
}

export function VersionHistory({ projectId, className }: VersionHistoryProps) {
  const [commits, setCommits] = useState<CommitEntry[]>([]);
  const [branch, setBranch] = useState("");
  const [head, setHead] = useState("");
  const [dirty, setDirty] = useState(false);
  const [changedFiles, setChangedFiles] = useState<string[]>([]);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [worktrees, setWorktrees] = useState<WorktreeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [snapshotting, setSnapshotting] = useState(false);
  const [tab, setTab] = useState<"commits" | "branches" | "worktrees">("commits");

  const loadVersions = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const [verRes, brRes, wtRes] = await Promise.all([
        api.fetchVersions(projectId, 20),
        api.fetchBranches(projectId),
        api.fetchWorktrees(projectId),
      ]);
      setCommits(verRes.commits ?? []);
      setBranch(verRes.branch ?? "");
      setHead(verRes.head ?? "");
      setDirty(verRes.dirty);
      setChangedFiles(verRes.changed_files ?? []);
      setBranches(brRes.branches ?? []);
      setWorktrees(wtRes.worktrees ?? []);
    } catch {
      // errors handled by parent
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadVersions();
  }, [loadVersions]);

  const handleSnapshot = async () => {
    if (!message.trim()) return;
    setSnapshotting(true);
    try {
      await api.createSnapshot(projectId, { message: message.trim() });
      setMessage("");
      await loadVersions();
    } catch {
      // toast handled by parent
    } finally {
      setSnapshotting(false);
    }
  };

  if (loading) {
    return (
      <div className={cn("flex items-center justify-center py-8", className)}>
        <Spinner />
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {/* Status bar */}
      <div className="flex items-center gap-2 text-xs text-text-secondary">
        <GitBranch className="h-3.5 w-3.5" />
        <span className="font-mono">{branch}</span>
        <span className="font-mono text-text-tertiary">{head}</span>
        {dirty && (
          <Badge tone="warning" className="text-xs">
            <AlertTriangle className="h-3 w-3 mr-0.5" />
            {changedFiles.length} changed
          </Badge>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-current/10 pb-1">
        {(["commits", "branches", "worktrees"] as const).map((t) => (
          <Button
            key={t}
            ghost
            size="sm"
            onClick={() => setTab(t)}
            className={cn(
              "text-xs capitalize",
              tab === t && "text-primary font-medium",
            )}
          >
            {t}
            {t === "commits" && ` (${commits.length})`}
            {t === "branches" && ` (${branches.length})`}
            {t === "worktrees" && ` (${worktrees.length})`}
          </Button>
        ))}
        <div className="flex-1" />
        <Button ghost size="sm" onClick={loadVersions} title="Refresh">
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Commits tab */}
      {tab === "commits" && (
        <>
          {/* Snapshot input */}
          <div className="flex gap-1">
            <input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSnapshot();
              }}
              placeholder="Snapshot message..."
              className="flex-1 min-w-0 rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary/50"
            />
            <Button
              size="sm"
              disabled={!message.trim() || snapshotting}
              onClick={handleSnapshot}
              prefix={snapshotting ? <Spinner /> : <Plus />}
            >
              Snapshot
            </Button>
          </div>

          {commits.length === 0 ? (
            <div className="py-4 text-center text-xs text-text-tertiary">
              No commits yet — create a snapshot to start version tracking
            </div>
          ) : (
            <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto">
              {commits.map((c) => (
                <div
                  key={c.hash}
                  className={cn(
                    "flex items-start gap-2 px-2 py-1.5 rounded text-xs group",
                    c.hash === head
                      ? "bg-primary/5"
                      : "hover:bg-muted/5",
                  )}
                >
                  {c.hash === head ? (
                    <Circle className="h-2.5 w-2.5 mt-0.5 text-primary fill-primary" />
                  ) : (
                    <GitCommit className="h-3 w-3 mt-0.5 text-text-tertiary" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-text-tertiary">
                        {c.hash}
                      </span>
                      <Clock className="h-3 w-3 text-text-tertiary" />
                      <span className="text-text-tertiary">{c.date}</span>
                    </div>
                    <div className="truncate text-text-secondary">{c.message}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Branches tab */}
      {tab === "branches" && (
        <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto">
          {branches.map((b) => (
            <div
              key={b.name}
              className={cn(
                "flex items-center gap-2 px-2 py-1.5 rounded text-xs",
                b.current ? "bg-primary/5 font-medium" : "hover:bg-muted/5",
              )}
            >
              {b.current ? (
                <Circle className="h-2.5 w-2.5 text-primary fill-primary" />
              ) : (
                <GitBranch className="h-3 w-3 text-text-tertiary" />
              )}
              <span className="font-mono flex-1 truncate">{b.name}</span>
              <span className="font-mono text-text-tertiary">{b.hash}</span>
            </div>
          ))}
        </div>
      )}

      {/* Worktrees tab */}
      {tab === "worktrees" && (
        <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto">
          {worktrees.length === 0 ? (
            <div className="py-4 text-center text-xs text-text-tertiary">
              No worktrees — create one for parallel revision workflows
            </div>
          ) : (
            worktrees.map((w) => (
              <div
                key={w.path}
                className="flex items-center gap-2 px-2 py-1.5 rounded text-xs hover:bg-muted/5"
              >
                <GitBranch className="h-3 w-3 text-text-tertiary" />
                <span className="font-mono flex-1 truncate">{w.branch}</span>
                <span className="font-mono text-text-tertiary">{w.hash}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
