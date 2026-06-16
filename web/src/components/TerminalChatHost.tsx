import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Copy } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { cn } from "@/lib/utils";
import { HERMES_BASE_PATH, buildWsAuthParam } from "@/lib/api";

const TERMINAL_THEME = {
  background: "#0d2626",
  foreground: "#f0e6d2",
  cursor: "#f0e6d2",
  cursorAccent: "#0d2626",
  selectionBackground: "#f0e6d244",
};

function terminalTierWidthPx(host: HTMLElement | null): number {
  if (typeof window === "undefined") return 1280;
  const fromHost = host?.clientWidth ?? 0;
  if (fromHost > 2) return Math.round(fromHost);
  const doc = document.documentElement?.clientWidth ?? 0;
  const vv = window.visualViewport;
  const inner = window.innerWidth;
  const vvw = vv?.width ?? inner;
  const layout = Math.min(inner, vvw, doc > 0 ? doc : inner);
  return Math.max(1, Math.round(layout));
}

function terminalFontSizeForWidth(layoutWidthPx: number): number {
  if (layoutWidthPx < 300) return 7;
  if (layoutWidthPx < 360) return 8;
  if (layoutWidthPx < 420) return 9;
  if (layoutWidthPx < 520) return 10;
  if (layoutWidthPx < 720) return 11;
  if (layoutWidthPx < 1024) return 12;
  return 14;
}

function terminalLineHeightForWidth(layoutWidthPx: number): number {
  return layoutWidthPx < 1024 ? 1.02 : 1.15;
}

function buildWsUrl(
  authParam: [string, string],
  resume: string | null,
  channel: string,
): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = new URLSearchParams({ [authParam[0]]: authParam[1], channel });
  if (resume) qs.set("resume", resume);
  return `${proto}//${window.location.host}${HERMES_BASE_PATH}/api/pty?${qs.toString()}`;
}

export { TERMINAL_THEME };

export interface TerminalChatHostHandle {
  sendToTerminal: (data: string) => void;
  isConnected: () => boolean;
}

export interface TerminalChatHostProps {
  isActive: boolean;
  channel: string;
  resumeParam: string | null;
}

export const TerminalChatHost = forwardRef<TerminalChatHostHandle, TerminalChatHostProps>(
  function TerminalChatHost({ isActive, channel, resumeParam }, ref) {
    const hostRef = useRef<HTMLDivElement | null>(null);
    const termRef = useRef<Terminal | null>(null);
    const fitRef = useRef<FitAddon | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const syncMetricsRef = useRef<(() => void) | null>(null);

    const [banner, setBanner] = useState<string | null>(() =>
      typeof window !== "undefined" && !window.__HERMES_SESSION_TOKEN__
        ? "Session token unavailable. Open this page through `hermes dashboard`, not directly."
        : null,
    );
    const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
    const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useImperativeHandle(ref, () => ({
      sendToTerminal: (data: string) => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(data);
      },
      isConnected: () =>
        wsRef.current !== null && wsRef.current.readyState === WebSocket.OPEN,
    }));

    const handleCopyLast = useCallback(() => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send("/copy");
      setTimeout(() => {
        const s = wsRef.current;
        if (s && s.readyState === WebSocket.OPEN) s.send("\r");
      }, 100);
      setCopyState("copied");
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      copyResetRef.current = setTimeout(() => setCopyState("idle"), 1500);
      termRef.current?.focus();
    }, []);

    // Main terminal + WebSocket setup effect.
    useEffect(() => {
      if (!isActive) return;

      const host = hostRef.current;
      if (!host) return;

      const token = window.__HERMES_SESSION_TOKEN__;
      if (!token) return;

      const tierW0 = terminalTierWidthPx(host);
      const term = new Terminal({
        allowProposedApi: true,
        cursorBlink: true,
        fontFamily:
          "'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'MesloLGS NF', 'Source Code Pro', Menlo, Consolas, 'DejaVu Sans Mono', monospace",
        fontSize: terminalFontSizeForWidth(tierW0),
        lineHeight: terminalLineHeightForWidth(tierW0),
        letterSpacing: 0,
        fontWeight: "400",
        fontWeightBold: "700",
        macOptionIsMeta: true,
        macOptionClickForcesSelection: true,
        rightClickSelectsWord: true,
        scrollback: 5000,
        theme: TERMINAL_THEME,
      });
      termRef.current = term;

      // --- Clipboard: OSC 52 handler ---
      term.parser.registerOscHandler(52, (data) => {
        const semi = data.indexOf(";");
        if (semi < 0) return false;
        const payload = data.slice(semi + 1);
        if (payload === "?" || payload === "") return false;
        try {
          const binary = atob(payload);
          const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
          const text = new TextDecoder("utf-8").decode(bytes);
          navigator.clipboard.writeText(text).catch((err) => {
            console.warn("[dashboard clipboard] OSC 52 write failed:", err.message);
          });
        } catch {
          console.warn("[dashboard clipboard] malformed OSC 52 payload");
        }
        return true;
      });

      const isMac =
        typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

      term.attachCustomKeyEventHandler((ev) => {
        if (ev.type !== "keydown") return true;

        const copyModifier = isMac ? ev.metaKey : ev.ctrlKey && ev.shiftKey;
        const pasteModifier = isMac ? ev.metaKey : ev.ctrlKey && ev.shiftKey;

        if (copyModifier && ev.key.toLowerCase() === "c") {
          const sel = term.getSelection();
          if (sel) {
            navigator.clipboard.writeText(sel).catch((err) => {
              console.warn("[dashboard clipboard] direct copy failed:", err.message);
            });
            term.clearSelection();
            ev.preventDefault();
            return false;
          }
        }

        if (pasteModifier && ev.key.toLowerCase() === "v") {
          navigator.clipboard
            .readText()
            .then((text) => {
              if (text) term.paste(text);
            })
            .catch((err) => {
              console.warn("[dashboard clipboard] paste failed:", err.message);
            });
          ev.preventDefault();
          return false;
        }

        return true;
      });

      const fit = new FitAddon();
      fitRef.current = fit;
      term.loadAddon(fit);

      term.attachCustomWheelEventHandler((ev) => {
        const delta = ev.deltaY;
        if (!delta) return false;
        const step = Math.max(1, Math.round(Math.abs(delta) / 50));
        term.scrollLines(delta > 0 ? step : -step);
        ev.preventDefault();
        ev.stopPropagation();
        return false;
      });

      const unicode11 = new Unicode11Addon();
      term.loadAddon(unicode11);
      term.unicode.activeVersion = "11";
      term.loadAddon(new WebLinksAddon());
      term.open(host);

      const useWebgl = terminalTierWidthPx(host) >= 768;
      if (useWebgl) {
        try {
          const webgl = new WebglAddon();
          webgl.onContextLoss(() => webgl.dispose());
          term.loadAddon(webgl);
        } catch (err) {
          console.warn("[hermes-chat] WebGL renderer unavailable; falling back to default", err);
        }
      }

      // Resize + metrics sync
      let hostSyncRaf = 0;
      const scheduleHostSync = () => {
        if (hostSyncRaf) return;
        hostSyncRaf = requestAnimationFrame(() => {
          hostSyncRaf = 0;
          syncTerminalMetrics();
        });
      };

      let metricsDebounce: ReturnType<typeof setTimeout> | null = null;
      const syncTerminalMetrics = () => {
        if (!host.isConnected || host.clientWidth <= 0 || host.clientHeight <= 0) return;
        const w = terminalTierWidthPx(host);
        const nextSize = terminalFontSizeForWidth(w);
        const nextLh = terminalLineHeightForWidth(w);
        const fontChanged =
          term.options.fontSize !== nextSize || term.options.lineHeight !== nextLh;
        if (fontChanged) {
          term.options.fontSize = nextSize;
          term.options.lineHeight = nextLh;
        }
        try {
          fit.fit();
        } catch {
          return;
        }
        if (fontChanged && term.rows > 0) {
          try {
            term.refresh(0, term.rows - 1);
          } catch {}
        }
        if (fontChanged && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(`\x1b[RESIZE:${term.cols};${term.rows}]`);
        }
      };
      syncMetricsRef.current = syncTerminalMetrics;

      const scheduleSyncTerminalMetrics = () => {
        if (metricsDebounce) clearTimeout(metricsDebounce);
        metricsDebounce = setTimeout(() => {
          metricsDebounce = null;
          syncTerminalMetrics();
        }, 60);
      };

      const ro = new ResizeObserver(() => scheduleHostSync());
      ro.observe(host);
      window.addEventListener("resize", scheduleSyncTerminalMetrics);
      window.visualViewport?.addEventListener("resize", scheduleSyncTerminalMetrics);
      scheduleHostSync();
      requestAnimationFrame(() => scheduleHostSync());

      let settleRaf1 = 0;
      let settleRaf2 = 0;
      settleRaf1 = requestAnimationFrame(() => {
        settleRaf1 = 0;
        settleRaf2 = requestAnimationFrame(() => {
          settleRaf2 = 0;
          syncTerminalMetrics();
        });
      });

      // WebSocket
      let unmounting = false;
      let onDataDisposable: { dispose(): void } | null = null;
      let onResizeDisposable: { dispose(): void } | null = null;
      void (async () => {
        const authParam = await buildWsAuthParam();
        if (unmounting) return;
        const url = buildWsUrl(authParam, resumeParam, channel);
        const ws = new WebSocket(url);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        ws.onopen = () => {
          setBanner(null);
          ws.send(`\x1b[RESIZE:${term.cols};${term.rows}]`);
        };

        ws.onmessage = (ev) => {
          if (typeof ev.data === "string") {
            term.write(ev.data);
          } else {
            term.write(new Uint8Array(ev.data as ArrayBuffer));
          }
        };

        ws.onclose = (ev) => {
          wsRef.current = null;
          if (unmounting) return;
          if (ev.code === 4401) {
            setBanner("Auth failed. Reload the page to refresh the session token.");
            return;
          }
          if (ev.code === 4403) {
            setBanner("Chat is only reachable from localhost.");
            return;
          }
          if (ev.code === 1011) return;
          term.write("\r\n\x1b[90m[session ended]\x1b[0m\r\n");
        };

        // eslint-disable-next-line no-control-regex
        const SGR_MOUSE_RE = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/;
        onDataDisposable = term.onData((data) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          if (SGR_MOUSE_RE.test(data)) return;
          ws.send(data);
        });

        onResizeDisposable = term.onResize(({ cols, rows }) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(`\x1b[RESIZE:${cols};${rows}]`);
          }
        });
      })();

      term.focus();

      return () => {
        unmounting = true;
        syncMetricsRef.current = null;
        onDataDisposable?.dispose();
        onResizeDisposable?.dispose();
        if (metricsDebounce) clearTimeout(metricsDebounce);
        window.removeEventListener("resize", scheduleSyncTerminalMetrics);
        window.visualViewport?.removeEventListener("resize", scheduleSyncTerminalMetrics);
        ro.disconnect();
        if (hostSyncRaf) cancelAnimationFrame(hostSyncRaf);
        if (settleRaf1) cancelAnimationFrame(settleRaf1);
        if (settleRaf2) cancelAnimationFrame(settleRaf2);
        wsRef.current?.close();
        wsRef.current = null;
        term.dispose();
        termRef.current = null;
        fitRef.current = null;
        if (copyResetRef.current) {
          clearTimeout(copyResetRef.current);
          copyResetRef.current = null;
        }
      };
    }, [channel, resumeParam, isActive]);

    // Visibility refit: when the tab re-activates, xterm needs a refit.
    useEffect(() => {
      if (!isActive) return;
      let raf1 = 0;
      let raf2 = 0;
      raf1 = requestAnimationFrame(() => {
        raf1 = 0;
        raf2 = requestAnimationFrame(() => {
          raf2 = 0;
          syncMetricsRef.current?.();
          const host = hostRef.current;
          const active =
            typeof document !== "undefined" ? document.activeElement : null;
          const focusIsElsewhereInChatPage =
            active !== null &&
            active !== document.body &&
            host !== null &&
            !host.contains(active);
          if (!focusIsElsewhereInChatPage) {
            termRef.current?.focus();
          }
        });
      });
      return () => {
        if (raf1) cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
      };
    }, [isActive]);

    return (
      <>
        {banner && (
          <div className="border border-warning/50 bg-warning/10 text-warning px-3 py-2 text-xs tracking-wide">
            {banner}
          </div>
        )}

        <div
          className={cn(
            "hermes-desktop-pane",
            "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg p-2 sm:p-3",
          )}
          style={{
            backgroundColor: TERMINAL_THEME.background,
            boxShadow: "0 8px 32px rgba(0, 0, 0, 0.25)",
          }}
        >
          <div
            ref={hostRef}
            className="hermes-chat-xterm-host min-h-0 min-w-0 flex-1"
          />

          <Button
            ghost
            onClick={handleCopyLast}
            title="Copy last assistant response as raw markdown"
            aria-label="Copy last assistant response"
            className={cn(
              "absolute z-10",
              "rounded border border-current/30",
              "bg-black/20 backdrop-blur-sm",
              "opacity-60 hover:opacity-100 hover:border-current/60",
              "transition-opacity duration-150 normal-case font-normal tracking-normal",
              "bottom-2 right-2 px-2 py-0.5 text-[0.65rem] sm:bottom-3 sm:right-3 sm:px-2.5 sm:py-1.5 sm:text-xs",
              "lg:bottom-4 lg:right-4",
            )}
            style={{ color: TERMINAL_THEME.foreground }}
          >
            <span className="inline-flex items-center gap-1.5">
              <Copy className="h-3 w-3 shrink-0" />
              <span className="hidden min-[400px]:inline tracking-wide">
                {copyState === "copied" ? "copied" : "copy last response"}
              </span>
            </span>
          </Button>
        </div>
      </>
    );
  },
);
