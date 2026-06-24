import { useNavigate } from "react-router-dom";
import { X, Plus, PanelRight, Folder } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatTabs } from "@/contexts/ChatTabContext";
import { ReasoningEffortPicker, type ReasoningEffort } from "@/components/ReasoningEffortPicker";
import type { ProjectInfo } from "@/lib/api";

export interface ChatTopBarProps {
  selectedProjectId: string;
  projects: ProjectInfo[];
  onSelectProject: (projectId: string) => void;
  sessionTitle: string | null;
  reasoningEffort: ReasoningEffort;
  onReasoningEffortChange: (e: ReasoningEffort) => void;
  effortDisabled?: boolean;
  rightPanelOpen: boolean;
  onToggleRightPanel: () => void;
  narrow?: boolean;
}

export function ChatTopBar({
  selectedProjectId,
  projects,
  onSelectProject,
  sessionTitle,
  reasoningEffort,
  onReasoningEffortChange,
  effortDisabled,
  rightPanelOpen,
  onToggleRightPanel,
  narrow,
}: ChatTopBarProps) {
  const { tabs, activeTabId, setActiveTab, addTab, closeTab } = useChatTabs();
  const navigate = useNavigate();

  const selectedProject = projects.find((p) => p.id === selectedProjectId);

  return (
    <div className="flex items-center shrink-0 border-b border-current/10 bg-background-base/40">
      {/* Tabs */}
      <div className="flex items-center min-w-0 flex-1 overflow-x-auto scrollbar-none">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            role="tab"
            aria-selected={activeTabId === tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "group flex items-center gap-1.5 h-8 px-3 text-[0.75rem] cursor-pointer select-none shrink-0",
              "border-r border-current/10 transition-colors",
              activeTabId === tab.id
                ? "bg-background-base text-foreground"
                : "text-muted-foreground hover:bg-muted/5",
            )}
          >
            <span className="truncate max-w-[120px]">{tab.title}</span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                closeTab(tab.id);
              }}
              className={cn(
                "h-4 w-4 flex items-center justify-center rounded-sm",
                "opacity-0 group-hover:opacity-100 hover:bg-muted/20 transition-opacity",
              )}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}

        <button
          type="button"
          onClick={() =>
            addTab({
              title: "New Chat",
              sessionId: null,
              projectId: selectedProjectId || null,
              projectName: selectedProject?.name ?? null,
              type: "native",
            })
          }
          className={cn(
            "h-8 w-8 flex items-center justify-center shrink-0",
            "text-muted-foreground hover:text-foreground hover:bg-muted/5 transition-colors",
          )}
          title="New chat tab"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2 shrink-0 pr-2">
        {/* Project selector */}
        <div className={cn("flex items-center gap-1 shrink-0", narrow && "hidden")}>
          <button
            type="button"
            disabled={!selectedProject}
            onClick={() =>
              selectedProject &&
              navigate(`/projects/${encodeURIComponent(selectedProject.id)}`)
            }
            className={cn(
              "flex items-center text-muted-foreground transition-colors",
              "hover:text-midground disabled:opacity-40 disabled:hover:text-muted-foreground",
            )}
            title={selectedProject ? `Open ${selectedProject.name}` : "No project selected"}
          >
            <Folder className="h-3 w-3 shrink-0" />
          </button>
          <select
            value={selectedProjectId}
            onChange={(e) => onSelectProject(e.target.value)}
            className={cn(
              "h-7 max-w-[160px] rounded border bg-background-base/60 px-1.5",
              "text-[0.65rem] text-muted-foreground cursor-pointer",
              "border-current/15 hover:border-primary/40 focus:border-primary/50 focus:outline-none",
            )}
            title="Switch project"
          >
            <option value="">(No project)</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        {sessionTitle && !narrow && (
          <span className="text-[0.65rem] text-muted-foreground/50 shrink-0 hidden lg:inline">
            &rsaquo; {sessionTitle}
          </span>
        )}

        {/* Reasoning Effort */}
        <ReasoningEffortPicker
          value={reasoningEffort}
          onChange={onReasoningEffortChange}
          disabled={effortDisabled}
        />

        {/* Right panel toggle */}
        <button
          type="button"
          onClick={onToggleRightPanel}
          className={cn(
            "flex items-center gap-1 rounded border px-2 py-1",
            "text-[0.65rem] transition-colors shrink-0",
            "hidden sm:flex",
            rightPanelOpen
              ? "border-primary/50 bg-primary/10 text-primary"
              : "border-current/15 text-muted-foreground hover:border-primary/40 hover:bg-primary/5",
          )}
          title="Toggle right panel"
        >
          <PanelRight className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}
