import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ProjectInfo, ProjectDetail, SessionInfo } from "@/lib/api";

const FAVORITES_KEY = "hermes-favorite-projects";

interface ProjectNode {
  project: ProjectInfo;
  sessions: SessionInfo[] | null;
  loading: boolean;
}

export interface UseProjectTreeResult {
  projects: ProjectNode[];
  recentSessions: SessionInfo[];
  loading: boolean;
  error: string | null;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  favorites: Set<string>;
  toggleFavorite: (projectId: string) => void;
  expandedIds: Set<string>;
  toggleExpand: (projectId: string) => void;
  deleteSession: (sessionId: string) => Promise<void>;
  deleteProject: (projectId: string) => Promise<void>;
  refresh: () => void;
}

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (raw) return new Set(JSON.parse(raw));
  } catch { /* ignore */ }
  return new Set();
}

function saveFavorites(fav: Set<string>): void {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...fav]));
  } catch { /* ignore */ }
}

export function useProjectTree(): UseProjectTreeResult {
  const [projectList, setProjectList] = useState<ProjectInfo[]>([]);
  const [projectDetails, setProjectDetails] = useState<Record<string, ProjectDetail>>({});
  const [recentSessions, setRecentSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [favorites, setFavorites] = useState<Set<string>>(loadFavorites);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectsRes, sessionsRes] = await Promise.all([
        api.fetchProjects(),
        api.getSessions(10, 0),
      ]);
      if (!mountedRef.current) return;
      setProjectList(projectsRes.projects);
      setRecentSessions(
        sessionsRes.sessions
          .filter((s) => s.message_count > 0)
          .sort((a, b) => b.last_active - a.last_active)
          .slice(0, 5),
      );
    } catch (e) {
      if (mountedRef.current) setError(String(e));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadData();
    return () => { mountedRef.current = false; };
  }, [loadData]);

  const toggleFavorite = useCallback((projectId: string) => {
    setFavorites((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      saveFavorites(next);
      return next;
    });
  }, []);

  const toggleExpand = useCallback(async (projectId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      return next;
    });

    if (!projectDetails[projectId]) {
      try {
        const detail = await api.getProject(projectId);
        if (mountedRef.current) {
          setProjectDetails((prev) => ({ ...prev, [projectId]: detail }));
        }
      } catch { /* ignore — will show empty */ }
    }
  }, [projectDetails]);

  const deleteSession = useCallback(async (sessionId: string) => {
    await api.deleteSession(sessionId);
    setRecentSessions((prev) => prev.filter((s) => s.id !== sessionId));
    setProjectDetails((prev) => {
      const next = { ...prev };
      for (const [pid, detail] of Object.entries(next)) {
        if (detail.sessions.some((s) => s.id === sessionId)) {
          next[pid] = {
            ...detail,
            sessions: detail.sessions.filter((s) => s.id !== sessionId),
            session_count: detail.session_count - 1,
          };
        }
      }
      return next;
    });
  }, []);

  const deleteProject = useCallback(async (projectId: string) => {
    await api.deleteProject(projectId);
    setProjectList((prev) => prev.filter((p) => p.id !== projectId));
    setProjectDetails((prev) => {
      const next = { ...prev };
      delete next[projectId];
      return next;
    });
  }, []);

  const projects: ProjectNode[] = useMemo(() => {
    let list = projectList.map((p): ProjectNode => {
      const detail = projectDetails[p.id];
      return {
        project: p,
        sessions: detail?.sessions ?? null,
        loading: expandedIds.has(p.id) && !detail,
      };
    });

    // favorites first
    list.sort((a, b) => {
      const aFav = favorites.has(a.project.id) ? 0 : 1;
      const bFav = favorites.has(b.project.id) ? 0 : 1;
      if (aFav !== bFav) return aFav - bFav;
      return (b.project.created ?? "").localeCompare(a.project.created ?? "");
    });

    // search filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((node) => {
        const nameMatch = node.project.name.toLowerCase().includes(q);
        const clientMatch = node.project.client?.toLowerCase().includes(q);
        const sessionMatch = node.sessions?.some(
          (s) => s.title?.toLowerCase().includes(q) || s.preview?.toLowerCase().includes(q),
        );
        return nameMatch || clientMatch || sessionMatch;
      });
    }

    return list;
  }, [projectList, projectDetails, favorites, expandedIds, searchQuery]);

  const filteredRecent = useMemo(() => {
    if (!searchQuery.trim()) return recentSessions;
    const q = searchQuery.toLowerCase();
    return recentSessions.filter(
      (s) => s.title?.toLowerCase().includes(q) || s.preview?.toLowerCase().includes(q) || s.id.includes(q),
    );
  }, [recentSessions, searchQuery]);

  return {
    projects,
    recentSessions: filteredRecent,
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
    refresh: loadData,
  };
}
