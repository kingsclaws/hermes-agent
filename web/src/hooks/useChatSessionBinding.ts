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
  findOrCreateTab,
}: UseChatSessionBindingOptions): ChatSessionBinding {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const projectParam = searchParams.get("project");
  const channel = useMemo(() => generateChannelId(), []);

  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
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
  const didAutoResume = useRef(false);
  const didInitTabs = useRef(false);
  const creatingSessionRef = useRef(false);
  const lastBoundProjectId = useRef<string | null>(null);

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

  // Sync project param from URL → state.
  useEffect(() => {
    if (!projectParam || projectParam === selectedProjectId) return;
    setSelectedProjectId(projectParam);
  }, [projectParam, selectedProjectId]);

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
        setSessions(sessionRes.sessions ?? []);
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
    if (!selectedProjectId) return;
    let cancelled = false;
    setSelectorBusy(true);
    setSelectorError(null);
    api
      .getProject(selectedProjectId)
      .then((project) => {
        if (cancelled) return;
        if (project.sessions?.length) {
          setSessions(project.sessions);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setSelectorBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId]);

  // Auto-resume: assign the most recent session using smart matching.
  useEffect(() => {
    if (!isActive || !activeTabId || didAutoResume.current || sessions.length === 0 || selectorBusy) return;
    if (selectedProjectId) return;
    const active = tabs.find((t) => t.id === activeTabId);
    if (active?.sessionId) return;
    didAutoResume.current = true;
    const withMessages = sessions.filter((s) => s.message_count > 0);
    const pick = withMessages[0] ?? sessions[0];
    if (pick) {
      findOrCreateTab(pick.id);
      updateChatSearch({ resume: pick.id });
    }
  }, [isActive, activeTabId, sessions, selectorBusy, selectedProjectId, findOrCreateTab, updateChatSearch]);

  // Project-binding: when a project is selected, bind it using smart matching.
  useEffect(() => {
    if (!isActive || !activeTabId || !selectedProjectId || selectorBusy) return;
    if (creatingSessionRef.current) return;
    // Bind only when the selected project actually changed. This effect also
    // re-fires whenever activeTabId changes (e.g. closing/switching tabs); without
    // this guard it would re-create a just-closed tab and re-lock the last project.
    if (selectedProjectId === lastBoundProjectId.current) return;

    const active = tabs.find((t) => t.id === activeTabId);
    if (!active) return;

    // Active tab already bound to this project — record it so future tab-close
    // events don't trigger a rebind.
    if (active.projectId === selectedProjectId && active.sessionId) {
      lastBoundProjectId.current = selectedProjectId;
      return;
    }

    lastBoundProjectId.current = selectedProjectId;

    if (sessions.length > 0) {
      const withMessages = sessions.filter((s) => s.message_count > 0);
      const pick = withMessages[0] ?? sessions[0];
      findOrCreateTab(pick.id, selectedProjectId);
      updateChatSearch({ project: selectedProjectId, resume: pick.id });
      return;
    }

    // No sessions for this project yet — create one pre-bound via the API
    // so the gateway session gets project_id set from the start.
    creatingSessionRef.current = true;
    api
      .createProjectSession(selectedProjectId)
      .then((res) => {
        creatingSessionRef.current = false;
        if (res?.session_id) {
          findOrCreateTab(res.session_id, selectedProjectId);
          updateChatSearch({ project: selectedProjectId, resume: res.session_id });
        }
      })
      .catch(() => {
        creatingSessionRef.current = false;
        updateTab(activeTabId, { projectId: selectedProjectId, sessionId: null });
      });
  }, [isActive, activeTabId, selectedProjectId, sessions, selectorBusy, findOrCreateTab, updateTab, updateChatSearch]);

  // Reset per-visit guards when leaving chat.
  useEffect(() => {
    if (!isActive) {
      didAutoResume.current = false;
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
      (active.projectId || null) !== (selectedProjectId || null)
    ) {
      updateChatSearch({ resume: active.sessionId, project: active.projectId });
    }
  }, [activeTabId, isActive]);

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
        if (activeTabId) updateTab(activeTabId, { sessionId: null });
        updateChatSearch({ resume: null });
        return;
      }
      const session = sessions.find((item) => item.id === sessionId);
      findOrCreateTab(sessionId, selectedProjectId || undefined);
      if (activeTabId) {
        updateTab(activeTabId, {
          sessionId,
          title: session?.title || sessionId.slice(0, 16) + "…",
        });
      }
      updateChatSearch({ resume: sessionId, project: selectedProjectId || null });
    },
    [activeTabId, findOrCreateTab, selectedProjectId, sessions, updateChatSearch, updateTab],
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
