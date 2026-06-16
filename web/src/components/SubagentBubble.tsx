import { getSwarmProfile } from "@/lib/swarmProfiles";
import { cn } from "@/lib/utils";
import { CheckCircle, XCircle, LoaderCircle } from "lucide-react";
import { useState } from "react";

export interface SubagentProps {
  id: string;
  goal: string;
  role?: string;
  profile?: string;
  model?: string;
  status: "running" | "done" | "error";
  toolName?: string;
  preview?: string;
  summary?: string;
  startedAt: number;
  completedAt?: number;
}

function statusIcon(status: SubagentProps["status"]) {
  switch (status) {
    case "running":
      return <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-amber-400" />;
    case "done":
      return <CheckCircle className="h-3 w-3 shrink-0 text-emerald-400" />;
    case "error":
      return <XCircle className="h-3 w-3 shrink-0 text-red-400" />;
  }
}

function elapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function SubagentBubble({ agent }: { agent: SubagentProps }) {
  const profile = getSwarmProfile(agent.profile ?? "");
  const Icon = profile.icon;
  const [expanded, setExpanded] = useState(false);

  const duration =
    agent.startedAt
      ? elapsed(Date.now() - agent.startedAt)
      : null;

  return (
    <div
      className={cn(
        "my-2 ml-2 mr-auto max-w-[88%] rounded-lg border text-xs",
        agent.status === "running"
          ? "border-amber-500/40 bg-amber-500/[0.04]"
          : agent.status === "error"
            ? "border-red-500/40 bg-red-500/[0.04]"
            : "border-emerald-500/30 bg-emerald-500/[0.03]",
      )}
    >
      {/* Header — always visible, click to expand */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate font-medium">{profile.name}</span>
        <span className="shrink-0">{statusIcon(agent.status)}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {agent.goal}
        </span>
        {agent.status === "running" && duration && (
          <span className="shrink-0 tabular-nums text-muted-foreground/60">
            {duration}
          </span>
        )}
        {agent.status !== "running" && agent.completedAt && (
          <span className="shrink-0 tabular-nums text-muted-foreground/60">
            {elapsed(agent.completedAt - agent.startedAt)}
          </span>
        )}
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-current/10 px-3 py-2 space-y-2">
          {/* Meta row */}
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[0.65rem] text-muted-foreground/70">
            {agent.role && <span>role: {agent.role}</span>}
            {agent.model && <span>model: {agent.model}</span>}
            {agent.toolName && <span>tool: {agent.toolName}</span>}
            {agent.profile && <span>profile: {agent.profile}</span>}
          </div>

          {/* Preview / thinking */}
          {agent.preview && (
            <div className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded bg-black/10 p-2 font-mono text-[0.7rem] leading-relaxed text-muted-foreground">
              {agent.preview}
            </div>
          )}

          {/* Summary */}
          {agent.summary && (
            <div className="whitespace-pre-wrap leading-relaxed text-muted-foreground">
              {agent.summary}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
