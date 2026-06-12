import { useState, useEffect, useRef, useCallback } from "react";
import { X, ArrowLeftRight, Minus, Plus, Edit3 } from "lucide-react";
import { api, type DiffPair, type DiffResponse } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn } from "@/lib/utils";

interface Props {
  projectId: string;
  original: string;
  revised: string;
  onClose(): void;
}

export function DiffViewer({ projectId, original, revised, onClose }: Props) {
  const [pairs, setPairs] = useState<DiffPair[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncScroll, setSyncScroll] = useState(true);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    api
      .diffFiles(projectId, original, revised, "sidebyside")
      .then((r: DiffResponse) => setPairs(r.pairs ?? []))
      .catch((e) => setError(e?.message ?? String(e)))
      .finally(() => setLoading(false));
  }, [projectId, original, revised]);

  const handleLeftScroll = useCallback(() => {
    if (!syncScroll || !leftRef.current || !rightRef.current) return;
    rightRef.current.scrollTop = leftRef.current.scrollTop;
  }, [syncScroll]);

  const handleRightScroll = useCallback(() => {
    if (!syncScroll || !leftRef.current || !rightRef.current) return;
    leftRef.current.scrollTop = rightRef.current.scrollTop;
  }, [syncScroll]);

  const changes = pairs.filter((p) => p.type !== "unchanged").length;
  const added = pairs.filter((p) => p.type === "added").length;
  const removed = pairs.filter((p) => p.type === "removed").length;
  const modified = pairs.filter((p) => p.type === "modified").length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-card border border-border shadow-2xl w-full max-w-6xl h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <div className="flex items-center gap-3 min-w-0">
            <ArrowLeftRight className="h-4 w-4 text-primary shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">
                {original.split("/").pop()} vs {revised.split("/").pop()}
              </div>
              <div className="text-[10px] text-text-tertiary flex items-center gap-2">
                <span className="flex items-center gap-0.5">
                  <Plus className="h-2.5 w-2.5 text-green-400" /> {added}
                </span>
                <span className="flex items-center gap-0.5">
                  <Minus className="h-2.5 w-2.5 text-red-400" /> {removed}
                </span>
                <span className="flex items-center gap-0.5">
                  <Edit3 className="h-2.5 w-2.5 text-yellow-400" /> {modified}
                </span>
                <span>{changes} change{changes !== 1 ? "s" : ""} total</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-text-tertiary cursor-pointer">
              <input
                type="checkbox"
                checked={syncScroll}
                onChange={(e) => setSyncScroll(e.target.checked)}
                className="rounded"
              />
              Sync scroll
            </label>
            <Button ghost size="icon" className="h-7 w-7" onClick={onClose}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Column headers */}
        <div className="grid grid-cols-2 border-b border-border">
          <div className="px-4 py-1.5 text-xs font-mono font-medium text-text-secondary border-r border-border truncate" title={original}>
            Original: {original}
          </div>
          <div className="px-4 py-1.5 text-xs font-mono font-medium text-text-secondary truncate" title={revised}>
            Revised: {revised}
          </div>
        </div>

        {/* Side-by-side content */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center text-sm text-destructive">{error}</div>
        ) : (
          <div className="flex-1 min-h-0 grid grid-cols-2 overflow-hidden">
            {/* Left panel */}
            <div
              ref={leftRef}
              onScroll={handleLeftScroll}
              className="overflow-y-auto border-r border-border"
            >
              {pairs.map((p, i) => (
                <div
                  key={i}
                  className={cn(
                    "px-3 py-1.5 text-xs font-mono leading-relaxed border-b border-border/30",
                    p.type === "removed" && "bg-red-950/20",
                    p.type === "modified" && "bg-yellow-950/10",
                    p.type === "added" && "bg-transparent text-text-tertiary",
                  )}
                >
                  {p.old_para != null && (
                    <span className="text-text-tertiary mr-2 select-none text-[10px]">
                      {p.old_para}
                    </span>
                  )}
                  <span className={cn(
                    p.type === "removed" && "text-red-300",
                    p.type === "modified" && "text-yellow-300",
                  )}>
                    {p.old_text || " "}
                  </span>
                </div>
              ))}
            </div>

            {/* Right panel */}
            <div
              ref={rightRef}
              onScroll={handleRightScroll}
              className="overflow-y-auto"
            >
              {pairs.map((p, i) => (
                <div
                  key={i}
                  className={cn(
                    "px-3 py-1.5 text-xs font-mono leading-relaxed border-b border-border/30",
                    p.type === "added" && "bg-green-950/20",
                    p.type === "modified" && "bg-yellow-950/10",
                    p.type === "removed" && "bg-transparent text-text-tertiary",
                  )}
                >
                  {p.new_para != null && (
                    <span className="text-text-tertiary mr-2 select-none text-[10px]">
                      {p.new_para}
                    </span>
                  )}
                  <span className={cn(
                    p.type === "added" && "text-green-300",
                    p.type === "modified" && "text-yellow-300",
                  )}>
                    {p.new_text || " "}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
