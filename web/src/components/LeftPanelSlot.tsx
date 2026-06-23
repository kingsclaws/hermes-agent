import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import ProjectTreePanel from "@/components/ProjectTreePanel";
import { useChatTabs } from "@/contexts/ChatTabContext";

export interface LeftPanelSlotProps {
  open: boolean;
}

export default function LeftPanelSlot({ open }: LeftPanelSlotProps) {
  const { findOrCreateTab } = useChatTabs();
  const navigate = useNavigate();

  const handleSelectSession = useCallback(
    (sessionId: string, projectId?: string) => {
      if (sessionId) {
        findOrCreateTab(sessionId, projectId);
      } else {
        // "New Chat" — create empty tab
        findOrCreateTab("", projectId);
      }
      // Navigate to chat view
      const params = new URLSearchParams();
      if (sessionId) params.set("resume", sessionId);
      if (projectId) params.set("project", projectId);
      const qs = params.toString();
      navigate(qs ? `/chat?${qs}` : "/chat");
    },
    [findOrCreateTab, navigate],
  );

  if (!open) return null;

  return (
    <div
      className={cn(
        "hermes-left-panel",
        "hidden lg:flex flex-col h-full shrink-0",
        "w-60 min-w-[200px] max-w-[400px]",
        "border-r border-current/20",
        "overflow-hidden",
      )}
      style={{
        background: "var(--component-sidebar-background)",
      }}
    >
      <ProjectTreePanel onSelectSession={handleSelectSession} />
    </div>
  );
}
