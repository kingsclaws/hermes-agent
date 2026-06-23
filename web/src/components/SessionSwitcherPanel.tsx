import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageSquare, Search, X } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { api, type SessionInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatRelativeDate(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp * 1000;
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

function groupByDate(sessions: SessionInfo[]): Map<string, SessionInfo[]> {
  const groups = new Map<string, SessionInfo[]>();
  const now = new Date();
  const today = now.toDateString();
  const yesterday = new Date(now.getTime() - 86_400_000).toDateString();

  for (const s of sessions) {
    const d = new Date(s.started_at * 1000).toDateString();
    let label: string;
    if (d === today) label = "Today";
    else if (d === yesterday) label = "Yesterday";
    else label = new Date(s.started_at * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });

    const list = groups.get(label) ?? [];
    list.push(s);
    groups.set(label, list);
  }
  return groups;
}

interface SessionSwitcherPanelProps {
  open: boolean;
  onClose: () => void;
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
}

export function SessionSwitcherPanel({
  open,
  onClose,
  currentSessionId,
  onSelectSession,
}: SessionSwitcherPanelProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getSessions(100, 0);
      setSessions(res.sessions);
    } catch {
      /* best effort */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void loadSessions();
  }, [open, loadSessions]);

  const filtered = useMemo(() => {
    if (!search.trim()) return sessions;
    const q = search.toLowerCase();
    return sessions.filter(
      (s) =>
        (s.title?.toLowerCase().includes(q)) ||
        (s.preview?.toLowerCase().includes(q)) ||
        s.id.toLowerCase().includes(q),
    );
  }, [sessions, search]);

  const groups = useMemo(() => groupByDate(filtered), [filtered]);

  if (!open) return null;

  return (
    <div className="flex h-full w-72 flex-col border-l border-current/15 bg-background-base/95 backdrop-blur-sm">
      <div className="flex items-center justify-between border-b border-current/10 px-3 py-2">
        <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
          Sessions
        </span>
        <Button
          ghost
          size="icon"
          onClick={onClose}
          aria-label="Close session panel"
          className="text-muted-foreground hover:text-foreground h-6 w-6"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="px-3 py-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions..."
            className="h-7 pl-7 text-xs"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1">
        {loading && (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            Loading sessions...
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            No sessions found
          </div>
        )}

        {!loading &&
          Array.from(groups.entries()).map(([label, items]) => (
            <div key={label} className="mb-2">
              <div className="sticky top-0 bg-background-base/95 px-3 py-1 text-[0.6rem] uppercase tracking-wider text-muted-foreground/70 font-medium">
                {label}
              </div>
              {items.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => {
                    onSelectSession(s.id);
                    onClose();
                  }}
                  className={cn(
                    "group w-full rounded px-3 py-2 text-left transition-colors",
                    "hover:bg-primary/8",
                    currentSessionId === s.id
                      ? "bg-primary/12 text-primary"
                      : "text-text-secondary",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium">
                        {s.title || "Untitled session"}
                      </div>
                      {s.preview && (
                        <div className="mt-0.5 truncate text-[0.6rem] text-muted-foreground">
                          {s.preview}
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-0.5">
                      <span className="text-[0.55rem] text-muted-foreground/60">
                        {formatRelativeDate(s.last_active)}
                      </span>
                      {s.is_active && (
                        <span className="h-1.5 w-1.5 rounded-full bg-success" />
                      )}
                    </div>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-[0.55rem] text-muted-foreground/50">
                    <span className="flex items-center gap-0.5">
                      <MessageSquare className="h-2.5 w-2.5" />
                      {s.message_count}
                    </span>
                    {s.model && (
                      <span className="truncate">{s.model}</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          ))}
      </div>
    </div>
  );
}
