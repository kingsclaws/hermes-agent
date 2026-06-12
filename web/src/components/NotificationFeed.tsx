import { GatewayClient } from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";
import {
  AlertCircle,
  Bell,
  CheckCircle2,
  FileWarning,
  Info,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

interface Notification {
  id: string;
  type: "info" | "success" | "warning" | "error";
  message: string;
  detail?: string;
  timestamp: number;
}

interface NotificationFeedProps {
  gw: GatewayClient | null;
  maxVisible?: number;
}

const ICON_MAP = {
  info: Info,
  success: CheckCircle2,
  warning: FileWarning,
  error: AlertCircle,
};

const TONE_MAP: Record<Notification["type"], string> = {
  info: "border-primary/30 bg-primary/5",
  success: "border-success/30 bg-success/5",
  warning: "border-warning/30 bg-warning/5",
  error: "border-destructive/30 bg-destructive/5",
};

const ICON_TONE: Record<Notification["type"], string> = {
  info: "text-primary",
  success: "text-success",
  warning: "text-warning",
  error: "text-destructive",
};

const TTL_MS = 8000;
const MAX_VISIBLE_DEFAULT = 5;

/**
 * Event-driven notification feed.
 *
 * Converts gateway events into dismissible toast notifications:
 * - background.complete → success notification
 * - error → error notification
 * - status.update with warnings → warning notification
 * - reasoning.available → brief info (first chunk only)
 */
export function NotificationFeed({
  gw,
  maxVisible = MAX_VISIBLE_DEFAULT,
}: NotificationFeedProps) {
  const [items, setItems] = useState<Notification[]>([]);

  const add = useCallback((n: Omit<Notification, "id" | "timestamp">) => {
    const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const notification: Notification = { ...n, id, timestamp: Date.now() };
    setItems((prev) => [notification, ...prev].slice(0, maxVisible));

    // Auto-dismiss after TTL
    setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id));
    }, TTL_MS);
  }, [maxVisible]);

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  useEffect(() => {
    if (!gw) return;

    const offBg = gw.on<{ message?: string; task_id?: string }>(
      "background.complete",
      (ev) => {
        const msg = ev.payload?.message ?? "Background task completed";
        add({ type: "success", message: msg, detail: ev.payload?.task_id });
      },
    );

    const offError = gw.on<{ message?: string }>("error", (ev) => {
      if (ev.payload?.message) {
        add({ type: "error", message: ev.payload.message });
      }
    });

    const offStatus = gw.on<{ kind?: string; text?: string }>(
      "status.update",
      (ev) => {
        const text = ev.payload?.text ?? "";
        const kind = ev.payload?.kind ?? "";
        if (kind === "warning" || text.toLowerCase().includes("warning")) {
          add({ type: "warning", message: text });
        }
      },
    );

    const offReasoning = gw.on<{ text?: string }>(
      "reasoning.available",
      () => {
        // Don't spam — reasoning events are for the ThinkingBlock UI,
        // not the notification feed.
      },
    );

    return () => {
      offBg();
      offError();
      offStatus();
      offReasoning();
    };
  }, [gw, add]);

  if (items.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {items.map((n) => {
        const Icon = ICON_MAP[n.type];
        return (
          <div
            key={n.id}
            className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 shadow-lg text-xs animate-in fade-in slide-in-from-right-2",
              TONE_MAP[n.type],
            )}
          >
            <Icon className={cn("h-3.5 w-3.5 shrink-0 mt-0.5", ICON_TONE[n.type])} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{n.message}</div>
              {n.detail && (
                <div className="text-[0.65rem] text-muted-foreground mt-0.5 truncate">
                  {n.detail}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismiss(n.id)}
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Hook that wires gateway events to a simple toast-like callback.
 * Used as a lighter alternative to the full NotificationFeed component.
 */
export function useNotificationToast(
  gw: GatewayClient | null,
  onToast: (message: string, type: "info" | "success" | "warning" | "error") => void,
) {
  useEffect(() => {
    if (!gw) return;

    const offBg = gw.on<{ message?: string }>("background.complete", (ev) => {
      if (ev.payload?.message) onToast(ev.payload.message, "success");
    });

    const offError = gw.on<{ message?: string }>("error", (ev) => {
      if (ev.payload?.message) onToast(ev.payload.message, "error");
    });

    return () => {
      offBg();
      offError();
    };
  }, [gw, onToast]);
}
