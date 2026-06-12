import { useState, useEffect } from "react";
import { X, FileText, Tag, History, Link } from "lucide-react";
import { api, type DocumentMeta, type FileRef } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn } from "@/lib/utils";
import { DocumentPreview } from "@/components/DocumentPreview";
import { DocumentMetadataEditor } from "@/components/DocumentMetadataEditor";
import { AuditTrail } from "@/components/AuditTrail";

type Tab = "preview" | "metadata" | "versions" | "refs";

interface VersionEntry {
  hash: string;
  date: string;
  author: string;
  message: string;
}

interface Props {
  projectId: string;
  filePath: string;
  fileName: string;
  meta?: DocumentMeta;
  onClose(): void;
  onMetaSaved(meta: DocumentMeta): void;
}

export function FileDetailPanel({
  projectId,
  filePath,
  fileName,
  meta,
  onClose,
  onMetaSaved,
}: Props) {
  const [tab, setTab] = useState<Tab>("preview");
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [refs, setRefs] = useState<FileRef[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [loadingRefs, setLoadingRefs] = useState(false);

  useEffect(() => {
    if (tab === "versions") {
      setLoadingVersions(true);
      api
        .fetchVersions(projectId)
        .then((r: any) => setVersions((r?.versions ?? []).slice(0, 20)))
        .catch(() => setVersions([]))
        .finally(() => setLoadingVersions(false));
    }
  }, [tab, projectId]);

  useEffect(() => {
    if (tab === "refs") {
      setLoadingRefs(true);
      api
        .fetchFileRefs(projectId, filePath)
        .then((r) => setRefs(r?.refs ?? []))
        .catch(() => setRefs([]))
        .finally(() => setLoadingRefs(false));
    }
  }, [tab, projectId, filePath]);

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "preview", label: "Preview", icon: <FileText className="h-3 w-3" /> },
    { id: "metadata", label: "Metadata", icon: <Tag className="h-3 w-3" /> },
    { id: "versions", label: "Versions", icon: <History className="h-3 w-3" /> },
    { id: "refs", label: "References", icon: <Link className="h-3 w-3" /> },
  ];

  return (
    <div className="flex flex-col h-full border-l border-border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border">
        <div className="min-w-0 flex-1">
          <div className="text-xs font-mono font-medium truncate">{fileName}</div>
          <div className="text-[10px] text-text-tertiary truncate">{filePath}</div>
        </div>
        <Button ghost size="icon" className="h-6 w-6 shrink-0 ml-2" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-1 px-3 py-2 text-xs border-b-2 transition-colors",
              tab === t.id
                ? "border-primary text-primary font-medium"
                : "border-transparent text-text-tertiary hover:text-text-secondary",
            )}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "preview" && (
          <DocumentPreview
            projectId={projectId}
            filePath={filePath}
            fileName={fileName}
            onClose={() => {}}
          />
        )}

        {tab === "metadata" && (
          <div className="p-3">
            <DocumentMetadataEditor
              projectId={projectId}
              filePath={filePath}
              currentMeta={meta}
              onClose={() => {}}
              onSaved={onMetaSaved}
            />
          </div>
        )}

        {tab === "versions" && (
          <div className="p-3">
            {loadingVersions ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                <Spinner className="text-xs" /> loading versions...
              </div>
            ) : versions.length === 0 ? (
              <div className="text-xs text-text-tertiary italic py-4">
                No version history available.
              </div>
            ) : (
              <div className="flex flex-col gap-1">
                {versions.map((v, i) => (
                  <div
                    key={v.hash}
                    className={cn(
                      "px-2 py-1.5 rounded text-xs border-l-2",
                      i === 0 ? "border-l-primary bg-primary/5" : "border-l-transparent hover:bg-secondary/20",
                    )}
                  >
                    <div className="font-mono text-[11px] truncate">{v.message || "(no message)"}</div>
                    <div className="flex items-center gap-2 text-text-tertiary mt-0.5">
                      <span className="font-mono text-[10px]">{v.hash.slice(0, 7)}</span>
                      <span>{v.author}</span>
                      <span>{v.date}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "refs" && (
          <div className="p-3">
            {loadingRefs ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                <Spinner className="text-xs" /> loading references...
              </div>
            ) : refs.length === 0 ? (
              <div className="text-xs text-text-tertiary italic py-4">
                Not linked to any checklist items or CPs.
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {refs.map((ref, i) => (
                  <div
                    key={i}
                    className="border border-border rounded p-2 text-xs"
                  >
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <Badge
                        tone={ref.type === "checklist_item" ? "outline" : "warning"}
                        className="text-[10px]"
                      >
                        {ref.type === "checklist_item" ? "Checklist" : "CP"}
                      </Badge>
                      {ref.type === "cp" && (
                        <Badge tone="outline" className="text-[10px]">
                          {ref.cp_status}
                        </Badge>
                      )}
                    </div>
                    <div className="text-text-secondary">
                      {ref.type === "checklist_item"
                        ? `${ref.checklist_name} > ${ref.item_text}`
                        : ref.cp_description}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-4 pt-4 border-t border-border">
              <AuditTrail projectId={projectId} filePath={filePath} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
