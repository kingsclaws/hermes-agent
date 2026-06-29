import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface ChatTab {
  id: string;
  title: string;
  sessionId: string | null;
  projectId: string | null;
  projectName: string | null;
  type: "native";
  createdAt: number;
  lastActivityAt: number;
}

export interface ChatTabContextValue {
  tabs: ChatTab[];
  activeTabId: string | null;
  setActiveTab: (id: string) => void;
  addTab: (tab: Omit<ChatTab, "id" | "createdAt" | "lastActivityAt">) => string;
  closeTab: (id: string) => void;
  updateTab: (id: string, patch: Partial<ChatTab>) => void;
  findOrCreateTab: (sessionId: string, projectId?: string) => string;
}

const ChatTabContext = createContext<ChatTabContextValue | null>(null);

let _nextId = 1;
function nextId(): string {
  return `tab-${_nextId++}`;
}

const TABS_KEY = "hermes-chat-tabs";
const ACTIVE_TAB_KEY = "hermes-chat-active-tab";

function loadTabs(): ChatTab[] {
  try {
    const raw = localStorage.getItem(TABS_KEY);
    if (!raw) return [];
    const tabs = JSON.parse(raw) as ChatTab[];
    return Array.isArray(tabs) ? tabs : [];
  } catch {
    return [];
  }
}

function saveTabs(tabs: ChatTab[]): void {
  try {
    localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
  } catch { /* ignore */ }
}

function loadActiveTabId(tabs: ChatTab[]): string | null {
  try {
    const saved = localStorage.getItem(ACTIVE_TAB_KEY);
    if (saved && tabs.some((t) => t.id === saved)) return saved;
  } catch { /* ignore */ }
  return tabs.length > 0 ? tabs[0].id : null;
}

function persistActiveTabId(id: string | null): void {
  try {
    if (id) localStorage.setItem(ACTIVE_TAB_KEY, id);
    else localStorage.removeItem(ACTIVE_TAB_KEY);
  } catch { /* ignore */ }
}

export function ChatTabProvider({ children }: { children: ReactNode }) {
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeTabId, setActiveTabIdRaw] = useState<string | null>(
    () => loadActiveTabId(tabs),
  );

  const persist = useCallback((updated: ChatTab[]) => {
    setTabs(updated);
    saveTabs(updated);
  }, []);

  const setActiveTab = useCallback((id: string) => {
    setActiveTabIdRaw(id);
    persistActiveTabId(id);
  }, []);

  const addTab = useCallback(
    (tab: Omit<ChatTab, "id" | "createdAt" | "lastActivityAt">): string => {
      const id = nextId();
      const now = Date.now();
      const created: ChatTab = { ...tab, id, createdAt: now, lastActivityAt: now };
      const updated = [...tabs, created];
      persist(updated);
      setActiveTab(id);
      return id;
    },
    [tabs, persist, setActiveTab],
  );

  const closeTab = useCallback(
    (id: string) => {
      const idx = tabs.findIndex((t) => t.id === id);
      if (idx < 0) return;
      const updated = tabs.filter((t) => t.id !== id);
      persist(updated);
      if (activeTabId === id) {
        const nextIdx = Math.min(idx, updated.length - 1);
        const nextId = updated.length > 0 ? updated[nextIdx].id : null;
        setActiveTabIdRaw(nextId);
        persistActiveTabId(nextId);
      }
    },
    [tabs, activeTabId, persist],
  );

  const updateTab = useCallback(
    (id: string, patch: Partial<ChatTab>) => {
      const updated = tabs.map((t) => (t.id === id ? { ...t, ...patch } : t));
      persist(updated);
    },
    [tabs, persist],
  );

  // Smart tab matching:
  //   (1) reuse if session already in a tab → switch to it
  //   (2) replace idle tab (no session, not active) → update in place
  //   (3) create new tab
  const findOrCreateTab = useCallback(
    (sessionId: string, projectId?: string): string => {
      const normalizedSessionId = sessionId || null;
      // (1) Reuse existing tab with this session
      if (normalizedSessionId) {
        const existing = tabs.find((t) => t.sessionId === normalizedSessionId);
        if (existing) {
          updateTab(existing.id, { lastActivityAt: Date.now() });
          setActiveTab(existing.id);
          return existing.id;
        }
      }

      // (2) Replace idle tab — no session, not the active tab
      const idleTab = tabs.find(
        (t) => t.id !== activeTabId && !t.sessionId,
      );
      if (idleTab) {
        const now = Date.now();
        updateTab(idleTab.id, {
          sessionId: normalizedSessionId,
          projectId: projectId ?? idleTab.projectId,
          title: normalizedSessionId ? `Session ${normalizedSessionId.slice(0, 8)}` : idleTab.title,
          lastActivityAt: now,
        });
        setActiveTab(idleTab.id);
        return idleTab.id;
      }

      // (3) Create a new tab
      return addTab({
        title: normalizedSessionId ? `Session ${normalizedSessionId.slice(0, 8)}` : "New Chat",
        sessionId: normalizedSessionId,
        projectId: projectId ?? null,
        projectName: null,
        type: "native",
      });
    },
    [tabs, activeTabId, addTab, updateTab, setActiveTab],
  );

  const value = useMemo<ChatTabContextValue>(
    () => ({ tabs, activeTabId, setActiveTab, addTab, closeTab, updateTab, findOrCreateTab }),
    [tabs, activeTabId, setActiveTab, addTab, closeTab, updateTab, findOrCreateTab],
  );

  return (
    <ChatTabContext.Provider value={value}>{children}</ChatTabContext.Provider>
  );
}

export function useChatTabs(): ChatTabContextValue {
  const ctx = useContext(ChatTabContext);
  if (!ctx) throw new Error("useChatTabs must be used within ChatTabProvider");
  return ctx;
}
