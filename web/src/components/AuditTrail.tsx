import { useState, useEffect } from "react";
import { Clock, User, FileText, PenLine, Download, Upload, CheckCircle } from "lucide-react";
import { api, type AuditEvent } from "@/lib/api";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { cn } from "@/lib/utils";

interface Props {
  projectId: string;
  filePath: string;
}

const ACTION_ICONS: Record<string, React.ReactNode> = {
  create: <FileText className="h-3 w-3" />,
  upload: <Upload className="h-3 w-3" />,
  download: <Download className="h-3 w-3" />,
  edit: <PenLine className="h-3 w-3" />,
  review: <CheckCircle className="h-3 w-3" />,
};

export function AuditTrail({ projectId, filePath }: Props) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [signingStatus, setSigningStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.fetchFileAudit(projectId, filePath)
      .then((r) => {
        setEvents(r.audit_log ?? []);
        setSigningStatus(r.signing_status ?? "unsigned");
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId, filePath]);

  const signingTones: Record<string, "success" | "warning" | "outline" | "info"> = {
    signed: "success",
    "partially-signed": "warning",
    sent: "info",
    unsigned: "outline",
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Signing status */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-tertiary">Signing status:</span>
        <Badge tone={signingTones[signingStatus] || "outline"} className="text-xs">
          {signingStatus || "unsigned"}
        </Badge>
      </div>

      {/* Audit timeline */}
      <div>
        <span className="text-xs font-medium text-text-secondary">Audit Trail</span>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
            <Spinner className="text-xs" /> loading...
          </div>
        ) : events.length === 0 ? (
          <div className="text-xs text-text-tertiary italic py-2">
            No audit events recorded.
          </div>
        ) : (
          <div className="relative mt-2">
            <div className="absolute left-[11px] top-2 bottom-2 w-px bg-border" />
            <div className="flex flex-col gap-2">
              {events.map((e, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <div className={cn(
                    "w-[23px] h-[23px] rounded-full flex items-center justify-center shrink-0 mt-0.5",
                    i === events.length - 1 ? "bg-primary/20" : "bg-secondary/60",
                  )}>
                    {ACTION_ICONS[e.action] || <Clock className="h-3 w-3 text-text-tertiary" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium">{e.action}</span>
                      <span className="text-text-tertiary">by {e.user}</span>
                    </div>
                    {e.detail && (
                      <div className="text-text-tertiary truncate">{e.detail}</div>
                    )}
                    <div className="text-[10px] text-text-tertiary mt-0.5">
                      {e.timestamp?.replace("T", " ").slice(0, 19)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
