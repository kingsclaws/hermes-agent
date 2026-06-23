import { useEffect, useMemo, useState } from "react";
import { CheckCircle, ChevronDown, ChevronRight, LoaderCircle, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { getSwarmProfile } from "@/lib/swarmProfiles";
import type { SubagentLine } from "@/components/NativeChatSurface";

export type SwarmState = "idle" | "active" | "completed";

export interface SwarmInlineViewProps {
  subagents: SubagentLine[];
  swarmState: SwarmState;
  className?: string;
}

function elapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function statusBadge(status: SubagentLine["status"]) {
  switch (status) {
    case "running":
      return <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-amber-400" />;
    case "done":
      return <CheckCircle className="h-3 w-3 shrink-0 text-emerald-400" />;
    case "error":
      return <XCircle className="h-3 w-3 shrink-0 text-red-400" />;
  }
}

function AgentColumn({ agent }: { agent: SubagentLine }) {
  const profile = getSwarmProfile(agent.profile ?? "");
  const Icon = profile.icon;
  const duration =
    agent.startedAt
      ? elapsed((agent.completedAt ?? Date.now()) - agent.startedAt)
      : null;

  return (
    <div
      className={cn(
        "flex flex-col min-w-0 rounded-lg border text-xs transition-colors",
        agent.status === "running"
          ? "border-amber-500/40 bg-amber-500/[0.04]"
          : agent.status === "error"
            ? "border-red-500/40 bg-red-500/[0.04]"
            : "border-emerald-500/30 bg-emerald-500/[0.03]",
      )}
    >
      {/* Column header */}
      <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-current/10">
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate font-medium min-w-0">{profile.name}</span>
        <span className="shrink-0 ml-auto">{statusBadge(agent.status)}</span>
        {duration && (
          <span className="shrink-0 tabular-nums text-[0.6rem] text-muted-foreground/60">
            {duration}
          </span>
        )}
      </div>

      {/* Goal */}
      <div className="px-2.5 py-1.5 text-[0.65rem] text-muted-foreground/80 border-b border-current/5 truncate">
        {agent.goal}
      </div>

      {/* Streaming output */}
      <div className="flex-1 min-h-[4rem] max-h-48 overflow-y-auto p-2.5 font-mono text-[0.65rem] leading-relaxed whitespace-pre-wrap break-all text-muted-foreground">
        {agent.preview || (
          <span className="text-muted-foreground/30 italic">
            {agent.status === "running" ? "Waiting for output…" : agent.status === "error" ? "Error — no output" : "Completed"}
          </span>
        )}
      </div>

      {/* Summary / error footer */}
      {agent.summary && agent.status !== "running" && (
        <div className="border-t border-current/5 px-2.5 py-1.5 text-[0.65rem] leading-relaxed text-muted-foreground/70">
          {agent.summary}
        </div>
      )}
    </div>
  );
}

function summaryLine(subagents: SubagentLine[]): string {
  if (subagents.length === 0) return "";
  const parts = subagents.map((a) => {
    const profile = getSwarmProfile(a.profile ?? "");
    const icon = a.status === "done" ? "✓" : a.status === "error" ? "✗" : "…";
    return `${profile.name} ${icon}`;
  });
  const done = subagents.filter((a) => a.status === "done").length;
  return `Swarm complete (${done}/${subagents.length}) — ${parts.join("  ")}`;
}

export function SwarmInlineView({ subagents, swarmState, className }: SwarmInlineViewProps) {
  const [collapsed, setCollapsed] = useState(false);

  // Auto-expand when swarm becomes active
  useEffect(() => {
    if (swarmState === "active") {
      setCollapsed(false);
    }
  }, [swarmState]);

  // Auto-collapse shortly after all agents complete
  useEffect(() => {
    if (swarmState !== "completed") return;
    const timer = setTimeout(() => setCollapsed(true), 1500);
    return () => clearTimeout(timer);
  }, [swarmState]);

  const idled = swarmState === "idle";
  const active = swarmState === "active";

  // Use memo to keep stable reference during streaming updates
  const firstStarted = useMemo(
    () => subagents.reduce((earliest, a) => Math.min(earliest, a.startedAt), Infinity),
    [subagents.length > 0 ? subagents[0].startedAt : null],
  );

  if (idled || subagents.length === 0) return null;

  const summary = summaryLine(subagents);

  return (
    <div className={cn("mt-2 rounded-lg border border-current/10 bg-muted/5", className)}>
      {/* Collapsed summary bar */}
      {collapsed && (
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className={cn(
            "flex w-full items-center gap-2 px-3 py-2 text-left text-xs",
            "hover:bg-muted/10 transition-colors rounded-lg",
          )}
        >
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          <span className="truncate text-muted-foreground">{summary}</span>
          {firstStarted < Infinity && (
            <span className="shrink-0 tabular-nums text-[0.6rem] text-muted-foreground/40">
              {elapsed(Date.now() - firstStarted)} total
            </span>
          )}
        </button>
      )}

      {/* Expanded grid */}
      {!collapsed && (
        <div>
          {/* Summary bar — click to collapse */}
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs",
              "hover:bg-muted/10 transition-colors",
              !active && "rounded-t-lg",
              active && "rounded-t-lg",
            )}
          >
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="truncate font-medium text-muted-foreground">
              {active ? "Swarm in progress" : "Swarm results"}
            </span>
            {!active && (
              <span className="shrink-0 text-[0.6rem] text-muted-foreground/40">
                click to collapse
              </span>
            )}
          </button>

          {/* Agent grid — 2 cols default, 3 cols on lg+ */}
          <div
            className={cn(
              "grid gap-2 p-2 pt-0",
              "grid-cols-1",
              subagents.length >= 2 && "sm:grid-cols-2",
              subagents.length >= 3 && "lg:grid-cols-3",
            )}
          >
            {subagents.map((agent) => (
              <AgentColumn key={agent.id} agent={agent} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
