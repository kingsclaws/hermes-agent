import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ChevronRight,
  FolderOpen,
  Folder,
  LayoutDashboard,
  MessageSquare,
  Plus,
  Search,
  Star,
  Trash2,
} from "lucide-react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn } from "@/lib/utils";
import { ContextMenu } from "@/components/ContextMenu";
import { ContextMenuItem, ContextMenuSeparator } from "@/components/ContextMenuItem";
import { useProjectTree } from "@/hooks/useProjectTree";
import { getPlatformIcon } from "@/lib/platform-icons";
import type { SessionInfo } from "@/lib/api";

interface ContextMenuState {
  x: number;
  y: number;
  type: "project" | "session";
  id: string;
  projectId?: string;
}

function relativeTime(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d`;
  return new Date(ts * 1000).toLocaleDateString();
}

export interface ProjectTreePanelProps {
  onSelectSession: (sessionId: string, projectId?: string) => void;
}

export default function ProjectTreePanel({ onSelectSession }: ProjectTreePanelProps) {
  const {
    projects,
    recentSessions,
    loading,
    error,
    searchQuery,
    setSearchQuery,
    favorites,
    toggleFavorite,
    expandedIds,
    toggleExpand,
    deleteSession,
    deleteProject,
    refresh,
  } = useProjectTree();

  const navigate = useNavigate();
  const [ctxMenu, setCtxMenu] = useState<ContextMenuState | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const handleSessionContext = useCallback((e: React.MouseEvent, session: SessionInfo, projectId?: string) => {
    e.preventDefault();
    setCtxMenu({ x: e.clientX, y: e.clientY, type: "session", id: session.id, projectId });
  }, []);

  const handleProjectContext = useCallback((e: React.MouseEvent, projectId: string) => {
    e.preventDefault();
    setCtxMenu({ x: e.clientX, y: e.clientY, type: "project", id: projectId });
  }, []);

  const handleDeleteSession = useCallback(async () => {
    if (!ctxMenu || ctxMenu.type !== "session") return;
    setDeleting(ctxMenu.id);
    setCtxMenu(null);
    try {
      await deleteSession(ctxMenu.id);
    } finally {
      setDeleting(null);
    }
  }, [ctxMenu, deleteSession]);

  const handleDeleteProject = useCallback(async () => {
    if (!ctxMenu || ctxMenu.type !== "project") return;
    setDeleting(ctxMenu.id);
    setCtxMenu(null);
    try {
      await deleteProject(ctxMenu.id);
    } finally {
      setDeleting(null);
    }
  }, [ctxMenu, deleteProject]);

  const handleOpenDashboard = useCallback(() => {
    if (!ctxMenu) return;
    const pid = ctxMenu.type === "project" ? ctxMenu.id : ctxMenu.projectId;
    setCtxMenu(null);
    if (pid) navigate(`/projects/${encodeURIComponent(pid)}`);
  }, [ctxMenu, navigate]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner className="text-text-tertiary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
        <span className="text-xs text-destructive">{error}</span>
        <button onClick={refresh} className="text-xs text-midground hover:underline cursor-pointer" type="button">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col min-h-0 overflow-hidden">
      {/* Search */}
      <div className="shrink-0 px-3 py-2 border-b border-current/10">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-text-tertiary" />
          <input
            type="text"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn(
              "w-full pl-7 pr-2 py-1.5 text-xs",
              "bg-background-base/50 border border-current/10 rounded",
              "text-text-primary placeholder:text-text-tertiary",
              "focus:outline-none focus:border-midground/30",
            )}
          />
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        {/* Recent Sessions */}
        {recentSessions.length > 0 && (
          <div className="py-1.5">
            <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-tertiary">
              Recent
            </div>
            {recentSessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                isDeleting={deleting === session.id}
                onClick={() => onSelectSession(session.id)}
                onContextMenu={(e) => handleSessionContext(e, session)}
              />
            ))}
          </div>
        )}

        {/* Projects */}
        {projects.length > 0 && (
          <div className="py-1.5 border-t border-current/10">
            <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-tertiary">
              Projects
            </div>
            {projects.map(({ project, sessions, loading: nodeLoading }) => {
              const isExpanded = expandedIds.has(project.id);
              const isFav = favorites.has(project.id);
              return (
                <div key={project.id}>
                  {/* Project row */}
                  <button
                    type="button"
                    className={cn(
                      "group flex w-full items-center gap-1.5 px-3 py-1.5 text-left cursor-pointer",
                      "hover:bg-midground/5 transition-colors",
                      isExpanded && "text-midground",
                    )}
                    onClick={() => toggleExpand(project.id)}
                    onContextMenu={(e) => handleProjectContext(e, project.id)}
                  >
                    <ChevronRight
                      className={cn(
                        "h-3 w-3 shrink-0 text-text-tertiary transition-transform duration-150",
                        isExpanded && "rotate-90",
                      )}
                    />
                    {isExpanded ? (
                      <FolderOpen className="h-3.5 w-3.5 shrink-0 text-text-secondary" />
                    ) : (
                      <Folder className="h-3.5 w-3.5 shrink-0 text-text-secondary" />
                    )}
                    <span className="flex-1 truncate text-xs">
                      {project.name}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); toggleFavorite(project.id); }}
                      className={cn(
                        "shrink-0 p-0.5 transition-opacity cursor-pointer",
                        isFav ? "text-yellow-500 opacity-100" : "text-text-tertiary opacity-0 group-hover:opacity-50",
                      )}
                      aria-label={isFav ? "Unstar" : "Star"}
                    >
                      <Star className={cn("h-3 w-3", isFav && "fill-current")} />
                    </button>
                    {project.doc_count != null && project.doc_count > 0 && (
                      <span className="shrink-0 text-[10px] text-text-tertiary tabular-nums">
                        {project.doc_count}
                      </span>
                    )}
                  </button>

                  {/* Expanded sessions */}
                  {isExpanded && (
                    <div className="ml-3 border-l border-current/10">
                      {nodeLoading ? (
                        <div className="flex items-center gap-2 px-4 py-2">
                          <Spinner className="text-[0.625rem] text-text-tertiary" />
                          <span className="text-[10px] text-text-tertiary">Loading...</span>
                        </div>
                      ) : sessions && sessions.length > 0 ? (
                        sessions
                          .sort((a, b) => b.last_active - a.last_active)
                          .map((session) => (
                            <SessionRow
                              key={session.id}
                              session={session}
                              isDeleting={deleting === session.id}
                              indent
                              onClick={() => onSelectSession(session.id, project.id)}
                              onContextMenu={(e) => handleSessionContext(e, session, project.id)}
                            />
                          ))
                      ) : (
                        <div className="px-4 py-2 text-[10px] text-text-tertiary italic">
                          No sessions
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {projects.length === 0 && recentSessions.length === 0 && (
          <div className="flex flex-1 items-center justify-center px-4 py-8">
            <span className="text-xs text-text-tertiary">No projects yet</span>
          </div>
        )}
      </div>

      {/* Bottom: New Session button */}
      <div className="shrink-0 border-t border-current/10 px-3 py-2">
        <button
          type="button"
          onClick={() => onSelectSession("")}
          className={cn(
            "flex w-full items-center justify-center gap-1.5 py-1.5",
            "text-xs text-text-secondary hover:text-midground",
            "border border-dashed border-current/20 rounded",
            "hover:border-midground/30 transition-colors cursor-pointer",
          )}
        >
          <Plus className="h-3 w-3" />
          New Chat
        </button>
      </div>

      {/* Context menu */}
      {ctxMenu && (
        <ContextMenu
          position={{ x: ctxMenu.x, y: ctxMenu.y }}
          onClose={() => setCtxMenu(null)}
        >
          {ctxMenu.type === "project" ? (
            <>
              <ContextMenuItem
                icon={<LayoutDashboard className="h-4 w-4" />}
                label="Open Dashboard"
                onClick={handleOpenDashboard}
              />
              <ContextMenuSeparator />
              <ContextMenuItem
                icon={<Trash2 className="h-4 w-4" />}
                label="Delete Project"
                variant="destructive"
                onClick={handleDeleteProject}
              />
            </>
          ) : (
            <>
              {ctxMenu.projectId && (
                <>
                  <ContextMenuItem
                    icon={<LayoutDashboard className="h-4 w-4" />}
                    label="Open Dashboard"
                    onClick={handleOpenDashboard}
                  />
                  <ContextMenuSeparator />
                </>
              )}
              <ContextMenuItem
                icon={<Trash2 className="h-4 w-4" />}
                label="Delete Session"
                variant="destructive"
                onClick={handleDeleteSession}
              />
            </>
          )}
        </ContextMenu>
      )}
    </div>
  );
}

function SessionRow({
  session,
  indent,
  isDeleting,
  onClick,
  onContextMenu,
}: {
  session: SessionInfo;
  indent?: boolean;
  isDeleting?: boolean;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
}) {
  const PlatformIcon = getPlatformIcon(session.source);
  const title = session.title || session.preview?.slice(0, 40) || `Session ${session.id.slice(0, 8)}`;

  return (
    <button
      type="button"
      className={cn(
        "group flex w-full items-center gap-2 text-left cursor-pointer",
        "hover:bg-midground/5 transition-colors",
        indent ? "pl-5 pr-3 py-1.5" : "px-3 py-1.5",
        isDeleting && "opacity-40 pointer-events-none",
      )}
      onClick={onClick}
      onContextMenu={onContextMenu}
    >
      <PlatformIcon className="h-3 w-3 shrink-0 text-text-tertiary" />
      <div className="flex-1 min-w-0">
        <div className="truncate text-xs text-text-secondary group-hover:text-midground">
          {title}
        </div>
      </div>
      <div className="shrink-0 flex items-center gap-1.5 text-[10px] text-text-tertiary tabular-nums">
        {session.message_count > 0 && (
          <span className="flex items-center gap-0.5">
            <MessageSquare className="h-2.5 w-2.5" />
            {session.message_count}
          </span>
        )}
        <span>{relativeTime(session.last_active)}</span>
      </div>
      {session.is_active && (
        <span className="shrink-0 h-1.5 w-1.5 rounded-full bg-success" title="Active" />
      )}
    </button>
  );
}
