declare global {
  interface Window {
    /** Native JSON-RPC chat is enabled by default; older servers may omit this. */
    __HERMES_DASHBOARD_CHAT__?: boolean;
    /** Set true by the server only for the legacy PTY/TUI chat bridge. */
    __HERMES_DASHBOARD_EMBEDDED_CHAT__?: boolean;
    /** @deprecated Older injected name; treated as on when true. */
    __HERMES_DASHBOARD_TUI__?: boolean;
  }
}

/** True when the native web chat route should be available. */
export function isDashboardChatEnabled(): boolean {
  if (typeof window === "undefined") return true;
  return window.__HERMES_DASHBOARD_CHAT__ !== false;
}

/** True only when the legacy embedded PTY/TUI bridge is enabled. */
export function isDashboardEmbeddedChatEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (window.__HERMES_DASHBOARD_EMBEDDED_CHAT__ === true) return true;
  return window.__HERMES_DASHBOARD_TUI__ === true;
}
