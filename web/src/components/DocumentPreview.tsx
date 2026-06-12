import { useEffect, useState } from "react";
import {
  FileText,
  X,
  AlertCircle,
  BarChart3,
} from "lucide-react";
import { api, type AnalyzeResponse, type PreviewResponse } from "@/lib/api";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { cn } from "@/lib/utils";

interface DocumentPreviewProps {
  projectId: string;
  filePath: string;
  fileName: string;
  onClose: () => void;
}

export function DocumentPreview({ projectId, filePath, fileName, onClose }: DocumentPreviewProps) {
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [tab, setTab] = useState<"preview" | "analysis">("preview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      api.previewFile(projectId, filePath, 30),
      api.analyzeFile(projectId, filePath),
    ])
      .then(([previewRes, analyzeRes]) => {
        if (cancelled) return;
        setPreview(previewRes);
        setAnalysis(analyzeRes);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e?.message ?? "Failed to load preview");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [projectId, filePath]);

  const stats = analysis?.stats;

  return (
    <div className="border border-border rounded-lg bg-card/60 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <FileText className="h-4 w-4 text-primary flex-shrink-0" />
        <span className="text-sm font-medium truncate flex-1 min-w-0">{fileName}</span>
        <div className="flex items-center gap-1">
          <Button
            ghost
            size="sm"
            className={cn("text-xs", tab === "preview" && "text-primary")}
            onClick={() => setTab("preview")}
          >
            Preview
          </Button>
          <Button
            ghost
            size="sm"
            className={cn("text-xs", tab === "analysis" && "text-primary")}
            onClick={() => setTab("analysis")}
            prefix={<BarChart3 className="h-3 w-3" />}
          >
            Analysis
          </Button>
          <Button ghost size="icon" className="h-6 w-6 ml-1" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Body */}
      <div className="max-h-80 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Spinner />
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 p-4 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            {error}
          </div>
        ) : tab === "preview" ? (
          <div className="p-3">
            {preview?.preview && preview.preview.length > 0 ? (
              <div className="flex flex-col gap-2">
                {preview.preview.map((para, i) => (
                  <p key={i} className="text-sm text-text-secondary leading-relaxed">
                    {para}
                  </p>
                ))}
              </div>
            ) : (
              <p className="text-xs text-text-tertiary py-8 text-center">
                No preview available for this file type.
              </p>
            )}
          </div>
        ) : (
          <div className="p-3">
            {stats && !analysis?.error ? (
              <div className="grid grid-cols-2 gap-3 text-xs">
                {stats.paragraphs != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Paragraphs</span>
                    <span className="font-mono font-medium">{stats.paragraphs}</span>
                  </div>
                )}
                {stats.tables != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Tables</span>
                    <span className="font-mono font-medium">{stats.tables}</span>
                  </div>
                )}
                {stats.tc_count != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Track Changes</span>
                    <span className="font-mono font-medium">{stats.tc_count}</span>
                  </div>
                )}
                {stats.comment_count != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Comments</span>
                    <span className="font-mono font-medium">{stats.comment_count}</span>
                  </div>
                )}
                {stats.lines != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Lines</span>
                    <span className="font-mono font-medium">{stats.lines}</span>
                  </div>
                )}
                {stats.chars != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Characters</span>
                    <span className="font-mono font-medium">{stats.chars.toLocaleString()}</span>
                  </div>
                )}
                {stats.size != null && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-text-tertiary">Size</span>
                    <span className="font-mono font-medium">{stats.size.toLocaleString()} B</span>
                  </div>
                )}
                {stats.fonts && stats.fonts.length > 0 && (
                  <div className="col-span-2 flex flex-col gap-1">
                    <span className="text-text-tertiary">Fonts</span>
                    <div className="flex flex-wrap gap-1">
                      {stats.fonts.map(([font, count]) => (
                        <Badge key={font} tone="outline" className="text-xs">
                          {font} ({count})
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {stats.message && (
                  <div className="col-span-2 text-text-tertiary italic">{stats.message}</div>
                )}
              </div>
            ) : (
              <p className="text-xs text-text-tertiary py-8 text-center">
                {analysis?.error ?? "Analysis not available for this file type."}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
