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
  type: "terminal" | "native";
  createdAt: number;
}

export interface ChatTabContextValue {
  tabs: ChatTab[];
  activeTabId: string | null;
  setActiveTab: (id: string) => void;
  addTab: (tab: Omit<ChatTab, "id" | "createdAt">) => string;
  closeTab: (id: string) => void;
  updateTab: (id: string, patch: Partial<ChatTab>) => void;
}

const ChatTabContext = createContext<ChatTabContextValue | null>(null);

let _nextId = 1;
function nextId(): string {
  return `tab-${_nextId++}`;
}

function loadTabs(): ChatTab[] {
  try {
    const raw = localStorage.getItem("hermes-chat-tabs");
    if (!raw) return [];
    const tabs = JSON.parse(raw) as ChatTab[];
    return Array.isArray(tabs) ? tabs : [];
  } catch {
    return [];
  }
}

function saveTabs(tabs: ChatTab[]): void {
  try {
    localStorage.setItem("hermes-chat-tabs", JSON.stringify(tabs));
  } catch { /* ignore */ }
}

export function ChatTabProvider({ children }: { children: ReactNode }) {
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeTabId, setActiveTabId] = useState<string | null>(
    () => tabs.length > 0 ? tabs[0].id : null,
  );

  const persist = useCallback((updated: ChatTab[]) => {
    setTabs(updated);
    saveTabs(updated);
  }, []);

  const addTab = useCallback(
    (tab: Omit<ChatTab, "id" | "createdAt">): string => {
      const id = nextId();
      const created: ChatTab = { ...tab, id, createdAt: Date.now() };
      const updated = [...tabs, created];
      persist(updated);
      setActiveTabId(id);
      return id;
    },
    [tabs, persist],
  );

  const closeTab = useCallback(
    (id: string) => {
      const idx = tabs.findIndex((t) => t.id === id);
      if (idx < 0) return;
      const updated = tabs.filter((t) => t.id !== id);
      persist(updated);
      if (activeTabId === id) {
        // Activate nearest tab
        const nextIdx = Math.min(idx, updated.length - 1);
        setActiveTabId(updated.length > 0 ? updated[nextIdx].id : null);
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

  const setActiveTab = useCallback((id: string) => {
    setActiveTabId(id);
  }, []);

  const value = useMemo<ChatTabContextValue>(
    () => ({ tabs, activeTabId, setActiveTab, addTab, closeTab, updateTab }),
    [tabs, activeTabId, setActiveTab, addTab, closeTab, updateTab],
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
