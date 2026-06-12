import { useCallback, useEffect, useState } from "react";
import { GatewayClient } from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";

interface ContextData {
  input_tokens?: number;
  output_tokens?: number;
  max_tokens?: number;
  cost_usd?: number;
}

interface ContextIndicatorProps {
  gw: GatewayClient | null;
  className?: string;
}

const WARN_PCT = 60;
const DANGER_PCT = 85;

/**
 * Context window usage indicator.
 *
 * Listens for token usage data from the gateway and renders a compact
 * progress bar showing context window fill. Colors shift from green →
 * amber → red as the context window fills.
 */
export function ContextIndicator({ gw, className }: ContextIndicatorProps) {
  const [ctx, setCtx] = useState<ContextData>({});
  const [visible, setVisible] = useState(false);

  const update = useCallback((data: ContextData) => {
    setCtx((prev) => {
      const next = { ...prev, ...data };
      const hasTokens =
        (next.input_tokens ?? 0) > 0 || (next.output_tokens ?? 0) > 0;
      setVisible(hasTokens);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!gw) return;

    // Listen to tool.progress for subagent token rollups
    const offProgress = gw.on<Record<string, unknown>>("tool.progress", (ev) => {
      const p = ev.payload ?? {};
      const hasTokens =
        typeof p.input_tokens === "number" || typeof p.output_tokens === "number";
      if (hasTokens) {
        update({
          input_tokens: p.input_tokens as number | undefined,
          output_tokens: p.output_tokens as number | undefined,
          max_tokens: p.max_tokens as number | undefined,
          cost_usd: p.cost_usd as number | undefined,
        });
      }
    });

    // Also listen to subagent.complete for aggregate stats
    const offSubagent = gw.on<Record<string, unknown>>(
      "subagent.complete",
      (ev) => {
        const p = ev.payload ?? {};
        const hasTokens =
          typeof p.input_tokens === "number" ||
          typeof p.output_tokens === "number";
        if (hasTokens) {
          update({
            input_tokens: (p.input_tokens as number) ?? ctx.input_tokens,
            output_tokens: (p.output_tokens as number) ?? ctx.output_tokens,
          });
        }
      },
    );

    const offSessionInfo = gw.on<Record<string, unknown>>(
      "session.info",
      (ev) => {
        if (ev.payload?.context_window) {
          setCtx((prev) => ({
            ...prev,
            max_tokens: ev.payload?.context_window as number,
          }));
        }
      },
    );

    return () => {
      offProgress();
      offSubagent();
      offSessionInfo();
    };
  }, [gw]);

  if (!visible || (!ctx.input_tokens && !ctx.output_tokens)) return null;

  const total = (ctx.input_tokens ?? 0) + (ctx.output_tokens ?? 0);
  const max = ctx.max_tokens ?? 200_000;
  const pct = Math.min(100, Math.round((total / max) * 100));
  const isWarn = pct >= WARN_PCT && pct < DANGER_PCT;
  const isDanger = pct >= DANGER_PCT;

  const barColor = isDanger
    ? "bg-destructive"
    : isWarn
      ? "bg-warning"
      : "bg-success";

  const textColor = isDanger
    ? "text-destructive"
    : isWarn
      ? "text-warning"
      : "text-success";

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 flex-1 rounded-full bg-muted/30 overflow-hidden min-w-[60px]">
        <div
          className={cn("h-full rounded-full transition-all duration-500", barColor)}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>
      <span className={cn("text-[0.6rem] tabular-nums font-mono shrink-0", textColor)}>
        {total >= 1000 ? `${(total / 1000).toFixed(0)}k` : total}
        {ctx.cost_usd ? ` · $${ctx.cost_usd.toFixed(2)}` : ""}
      </span>
    </div>
  );
}
