import { useChatTabs } from "@/contexts/ChatTabContext";
import { cn } from "@/lib/utils";
import { X, Plus } from "lucide-react";

export function ChatTabBar() {
  const { tabs, activeTabId, setActiveTab, addTab, closeTab } = useChatTabs();

  return (
    <div className="flex items-center border-b border-current/10 bg-background-base/40 shrink-0 overflow-x-auto scrollbar-none">
      <div className="flex items-center min-w-0 flex-1">
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
              type: "terminal",
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
    </div>
  );
}
