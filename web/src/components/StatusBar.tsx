import { useEffect, useState } from "react";
import { useStatusBar } from "@/contexts/StatusBarContext";
import { cn } from "@/lib/utils";

export function StatusBar() {
  const { connectionStatus, sessionId, projectName, gitBranch, tokenUsage, lastActivity } =
    useStatusBar();
  const [clock, setClock] = useState("");

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setClock(
        now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }),
      );
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "z-30 flex h-6 shrink-0 items-center justify-between gap-2 px-3",
        "hermes-desktop-statusbar",
        "border-t border-current/10 bg-background-base/95",
        "text-[0.65rem] font-mono-ui text-muted-foreground",
      )}
    >
      <div className="flex items-center gap-3 min-w-0">
        <span className="flex items-center gap-1 shrink-0">
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              connectionStatus === "connected" && "bg-green-500",
              connectionStatus === "disconnected" && "bg-red-500",
              connectionStatus === "connecting" && "bg-yellow-500 animate-pulse",
            )}
          />
          {sessionId ? (
            <span className="truncate max-w-[160px]">{sessionId}</span>
          ) : (
            <span>No session</span>
          )}
        </span>

        {projectName && (
          <span className="flex items-center gap-1 shrink-0">
            <span className="opacity-40">▸</span>
            <span className="truncate max-w-[180px]">{projectName}</span>
            {gitBranch && (
              <span className="opacity-60 truncate max-w-[100px]">{gitBranch}</span>
            )}
          </span>
        )}

        {lastActivity && (
          <span className="hidden sm:inline truncate opacity-60">{lastActivity}</span>
        )}
      </div>

      <div className="flex items-center gap-3 shrink-0">
        {tokenUsage && (
          <span className="hidden sm:inline tabular-nums">
            {tokenUsage.used.toLocaleString()} / {tokenUsage.total.toLocaleString()} tok
          </span>
        )}
        <span className="tabular-nums opacity-60">{clock}</span>
      </div>
    </div>
  );
}
