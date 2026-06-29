import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import type { ProjectInfo, SessionInfo } from "@/lib/api";
import type { NativeProjectContext } from "@/components/NativeChatSurface";
import type { ChatTab, ChatTabContextValue } from "@/contexts/ChatTabContext";

const CHAT_PROJECT_KEY = "hermes.lex.chat.project";

function generateChannelId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `chat-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

export interface UseChatSessionBindingOptions {
  isActive: boolean;
  tabs: ChatTab[];
  activeTabId: string | null;
  addTab: ChatTabContextValue["addTab"];
  updateTab: ChatTabContextValue["updateTab"];
  findOrCreateTab: ChatTabContextValue["findOrCreateTab"];
}

export interface ChatSessionBinding {
  projects: ProjectInfo[];
  sessions: SessionInfo[];
  selectedProjectId: string;
  selectedProject: NativeProjectContext;
  selectorError: string | null;
  selectorBusy: boolean;
  resumeParam: string | null;
  channel: string;
  creatingSessionRef: React.RefObject<boolean>;
  handleSelectProject: (projectId: string) => void;
  handleSelectSession: (sessionId: string) => void;
  updateChatSearch: (updates: Record<string, string | null>) => void;
}

export function useChatSessionBinding({
  isActive,
  tabs,
  activeTabId,
  addTab,
  updateTab,
}: UseChatSessionBindingOptions): ChatSessionBinding {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const projectParam = searchParams.get("project");
  const channel = useMemo(() => generateChannelId(), []);

  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [recentSessions, setRecentSessions] = useState<SessionInfo[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>(() => {
    if (projectParam) return projectParam;
    try {
      return localStorage.getItem(CHAT_PROJECT_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [selectorError, setSelectorError] = useState<string | null>(null);
  const [selectorBusy, setSelectorBusy] = useState(true);
  const didInitTabs = useRef(false);
  const creatingSessionRef = useRef(false);

  // Stable URL updater: reads searchParams via ref to avoid identity changes.
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;

  const updateChatSearch = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParamsRef.current);
      for (const [key, value] of Object.entries(updates)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      setSearchParams(next, { replace: true });
    },
    [setSearchParams],
  );

  // Sync explicit URL params into state/tab. This is the only URL → UI binding
  // path; ordinary tab switches flow in the opposite direction below.
  useEffect(() => {
    if (!isActive || (!projectParam && !resumeParam)) return;
    const nextProjectId = projectParam ?? "";
    if (nextProjectId !== selectedProjectId) {
      setSelectedProjectId(nextProjectId);
    }
    const active = tabs.find((t) => t.id === activeTabId);
    if (
      active &&
      ((active.projectId ?? null) !== (projectParam ?? null) ||
        (active.sessionId ?? null) !== (resumeParam ?? null))
    ) {
      updateTab(active.id, {
        projectId: projectParam ?? null,
        sessionId: resumeParam ?? null,
      });
    }
  }, [activeTabId, isActive, projectParam, resumeParam, selectedProjectId, tabs, updateTab]);

  // Active tab is the source of truth for the top-bar selectors. This prevents
  // stale localStorage/URL state from rebinding a newly opened tab to an old
  // project and causing visible project/session jumping.
  useEffect(() => {
    if (!isActive || projectParam) return;
    const active = tabs.find((t) => t.id === activeTabId);
    if (!active) return;
    const nextProjectId = active.projectId ?? "";
    if (nextProjectId !== selectedProjectId) {
      setSelectedProjectId(nextProjectId);
    }
  }, [activeTabId, isActive, projectParam, selectedProjectId, tabs]);

  // Initial load: projects + recent sessions.
  useEffect(() => {
    let cancelled = false;
    setSelectorBusy(true);
    setSelectorError(null);

    Promise.all([
      api.fetchProjects().catch((e: any) => {
        throw new Error(e?.message ?? "Failed to load projects");
      }),
      api.getSessions(50).catch((e: any) => {
        throw new Error(e?.message ?? "Failed to load sessions");
      }),
    ])
      .then(([projectRes, sessionRes]) => {
        if (cancelled) return;
        setProjects(projectRes.projects ?? []);
        const loadedSessions = sessionRes.sessions ?? [];
        setRecentSessions(loadedSessions);
        setSessions(loadedSessions);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setSelectorError(e?.message ?? "Failed to load chat context");
      })
      .finally(() => {
        if (!cancelled) setSelectorBusy(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // When a project is selected, fetch its sessions.
  useEffect(() => {
    if (!selectedProjectId) {
      setSessions(recentSessions);
      return;
    }
    let cancelled = false;
    setSelectorBusy(true);
    setSelectorError(null);
    api
      .getProject(selectedProjectId)
      .then((project) => {
        if (cancelled) return;
        setSessions(project.sessions ?? []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setSelectorBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [recentSessions, selectedProjectId]);

  // Reset per-visit guards when leaving chat.
  useEffect(() => {
    if (!isActive) {
      creatingSessionRef.current = false;
      didInitTabs.current = false;
    }
  }, [isActive]);

  // Create a default tab on first visit.
  useEffect(() => {
    if (!isActive || didInitTabs.current) return;
    if (tabs.length === 0) {
      didInitTabs.current = true;
      addTab({
        title: "New Chat",
        sessionId: null,
        projectId: null,
        projectName: null,
        type: "native",
      });
    } else {
      didInitTabs.current = true;
    }
  }, [isActive, tabs.length, addTab]);

  // Tab→URL sync: when active tab changes, update URL for bookmarkability.
  useEffect(() => {
    if (!isActive) return;
    const active = tabs.find((t) => t.id === activeTabId);
    if (!active) return;

    if (
      active.sessionId !== resumeParam ||
      (active.projectId || null) !== (projectParam || null)
    ) {
      updateChatSearch({ resume: active.sessionId, project: active.projectId });
    }
  }, [activeTabId, isActive, projectParam, resumeParam, tabs, updateChatSearch]);

  // Derive tab title from session/project.
  const selectedProject = useMemo<NativeProjectContext>(() => {
    if (!selectedProjectId) return null;
    const project = projects.find((item) => item.id === selectedProjectId);
    if (!project) {
      return { id: selectedProjectId, name: selectedProjectId };
    }
    return {
      id: project.id,
      name: project.name,
      client: project.client,
      goal: project.goal,
      directory: project.directory,
      cwd: project.cwd,
      status: project.status,
    };
  }, [projects, selectedProjectId]);

  useEffect(() => {
    if (!isActive || !activeTabId) return;
    const active = tabs.find((t) => t.id === activeTabId);
    if (!active) return;

    let title: string | null = null;
    if (selectedProject && typeof selectedProject === "object") {
      title = selectedProject.name || null;
    } else if (resumeParam) {
      title = resumeParam.slice(0, 16) + "…";
    }

    if (title && active.title === "New Chat") {
      updateTab(activeTabId, { title });
    }
  }, [resumeParam, selectedProject, selectedProjectId]);

  const handleSelectProject = useCallback(
    (projectId: string) => {
      creatingSessionRef.current = false;
      setSelectedProjectId(projectId);
      try {
        if (projectId) localStorage.setItem(CHAT_PROJECT_KEY, projectId);
        else localStorage.removeItem(CHAT_PROJECT_KEY);
      } catch {}
      if (activeTabId) {
        updateTab(activeTabId, { projectId: projectId || null, sessionId: null });
      }
      updateChatSearch({ project: projectId || null, resume: null });
    },
    [activeTabId, updateChatSearch, updateTab],
  );

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      if (!sessionId) {
        creatingSessionRef.current = false;
        if (activeTabId) updateTab(activeTabId, { sessionId: null });
        updateChatSearch({ resume: null });
        return;
      }
      const session = sessions.find((item) => item.id === sessionId);
      creatingSessionRef.current = false;
      if (activeTabId) {
        updateTab(activeTabId, {
          sessionId,
          title: session?.title || sessionId.slice(0, 16) + "…",
        });
      }
      updateChatSearch({ resume: sessionId, project: selectedProjectId || null });
    },
    [activeTabId, selectedProjectId, sessions, updateChatSearch, updateTab],
  );

  return {
    projects,
    sessions,
    selectedProjectId,
    selectedProject,
    selectorError,
    selectorBusy,
    resumeParam,
    channel,
    creatingSessionRef,
    handleSelectProject,
    handleSelectSession,
    updateChatSearch,
  };
}
