import { Button } from "@nous-research/ui/ui/components/button";
import { Card } from "@nous-research/ui/ui/components/card";
import { GatewayClient } from "@/lib/gatewayClient";
import { AlertTriangle, Check, Shield, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

interface ApprovalRequest {
  command?: string;
  description?: string;
  pattern_key?: string;
  pattern_keys?: string[];
}

interface ApprovalModalProps {
  gw: GatewayClient | null;
  sessionId: string | null;
}

/**
 * Modal for permission approval requests.
 *
 * Listens for approval.request, sudo.request, and secret.request events
 * from the gateway and renders an interactive dialog. The backend BLOCKS
 * until the frontend responds — every second counts for UX.
 */
export function ApprovalModal({ gw, sessionId }: ApprovalModalProps) {
  const [pending, setPending] = useState<{
    type: string;
    requestId?: string;
    command?: string;
    description?: string;
    envVar?: string;
    prompt?: string;
  } | null>(null);
  const [responding, setResponding] = useState(false);

  const dismiss = useCallback(
    (response: string) => {
      if (!gw || !pending) return;
      setResponding(true);

      const method =
        pending.type === "sudo.request"
          ? "sudo.respond"
          : pending.type === "secret.request"
            ? "secret.respond"
            : "approval.respond";

      const params: Record<string, unknown> = {};
      if (pending.requestId) params.request_id = pending.requestId;

      if (method === "approval.respond") {
        params.choice = response;
      } else {
        params.answer = response;
      }

      void gw
        .request(method, params, 10_000)
        .then(() => {
          setPending(null);
          setResponding(false);
        })
        .catch(() => {
          setPending(null);
          setResponding(false);
        });
    },
    [gw, pending],
  );

  useEffect(() => {
    if (!gw) return;

    const offApproval = gw.on<ApprovalRequest>("approval.request", (ev) => {
      if (!ev.payload) return;
      setPending({
        type: "approval.request",
        command: ev.payload.command,
        description: ev.payload.description,
      });
    });

    const offSudo = gw.on<{ request_id?: string; prompt?: string }>(
      "sudo.request",
      (ev) => {
        setPending({
          type: "sudo.request",
          requestId: ev.payload?.request_id,
          prompt: ev.payload?.prompt ?? "sudo password required",
        });
      },
    );

    const offSecret = gw.on<{
      request_id?: string;
      env_var?: string;
      prompt?: string;
    }>("secret.request", (ev) => {
      setPending({
        type: "secret.request",
        requestId: ev.payload?.request_id,
        envVar: ev.payload?.env_var,
        prompt: ev.payload?.prompt ?? "API key or secret required",
      });
    });

    return () => {
      offApproval();
      offSudo();
      offSecret();
    };
  }, [gw]);

  if (!pending) return null;

  const isApproval = pending.type === "approval.request";
  const isSecret = pending.type === "secret.request";
  const isSudo = pending.type === "sudo.request";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={() => !responding && dismiss(isApproval ? "deny" : "")}
      />

      <Card className="relative z-10 w-full max-w-lg border-primary/30 bg-background-base shadow-2xl normal-case">
        <div className="flex items-center gap-2 border-b border-current/10 px-4 py-3">
          {isApproval ? (
            <Shield className="h-4 w-4 text-warning" />
          ) : (
            <AlertTriangle className="h-4 w-4 text-warning" />
          )}
          <span className="text-sm font-semibold">
            {isApproval
              ? "Permission Required"
              : isSudo
                ? "Sudo Password Required"
                : "Secret Required"}
          </span>
        </div>

        <div className="px-4 py-3 space-y-3">
          {isApproval && (
            <>
              {pending.command && (
                <div>
                  <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground mb-1">
                    Command
                  </div>
                  <code className="block rounded bg-black/10 px-2 py-1.5 text-xs font-mono break-all">
                    {pending.command}
                  </code>
                </div>
              )}
              {pending.description && (
                <div>
                  <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground mb-1">
                    Description
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {pending.description}
                  </p>
                </div>
              )}
            </>
          )}

          {isSudo && (
            <p className="text-sm text-muted-foreground">{pending.prompt}</p>
          )}

          {isSecret && (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">{pending.prompt}</p>
              {pending.envVar && (
                <div className="text-[0.65rem] text-muted-foreground">
                  Will be stored as:{" "}
                  <code className="text-primary">{pending.envVar}</code>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-current/10 px-4 py-3">
          {isApproval ? (
            <>
              <Button
                outlined
                size="sm"
                disabled={responding}
                onClick={() => dismiss("deny")}
                prefix={<X className="h-3.5 w-3.5" />}
              >
                Deny
              </Button>
              <Button
                outlined
                size="sm"
                disabled={responding}
                onClick={() => dismiss("once")}
                prefix={<Check className="h-3.5 w-3.5" />}
              >
                Allow Once
              </Button>
              <Button
                size="sm"
                disabled={responding}
                onClick={() => dismiss("always")}
                prefix={<Check className="h-3.5 w-3.5" />}
              >
                Always Allow
              </Button>
            </>
          ) : isSudo ? (
            <>
              <Button
                outlined
                size="sm"
                disabled={responding}
                onClick={() => dismiss("")}
              >
                Skip
              </Button>
              <Button
                size="sm"
                disabled={responding}
                onClick={() => dismiss("ok")}
              >
                Continue
              </Button>
            </>
          ) : (
            <>
              <Button
                outlined
                size="sm"
                disabled={responding}
                onClick={() => dismiss("")}
              >
                Skip
              </Button>
              <Button
                size="sm"
                disabled={responding}
                onClick={() => dismiss("ok")}
              >
                Provide
              </Button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
