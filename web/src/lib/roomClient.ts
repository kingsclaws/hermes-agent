/** Lightweight WebSocket client for the legal swarm chat room protocol.

The room protocol is simple JSON (not JSON-RPC):

Inbound (server → client):  ``RoomMessage`` objects
Outbound (client → server):
  ``{type: "user_msg", text: "..."}``  — broadcast a user message
  ``{type: "replay"}``                — request full message history

Auth uses the same token/ticket pattern as GatewayClient.
*/

import { HERMES_BASE_PATH, getWsTicket } from "./api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RoomMessage {
  id: string;
  kind: "user" | "bot" | "status" | "system";
  text: string;
  sender?: string;
  senderProfile?: string;
  taskId?: string;
  timestamp: number;
}

export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";

// ---------------------------------------------------------------------------
// RoomClient
// ---------------------------------------------------------------------------

export class RoomClient {
  private ws: WebSocket | null = null;
  private _state: ConnectionState = "idle";
  private stateListeners = new Set<(s: ConnectionState) => void>();
  private messageListeners = new Set<(msg: RoomMessage) => void>();
  private _roomId = "";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _reconnectMs = 1000;

  // ── State management ──────────────────────────────────────────────────

  get state(): ConnectionState {
    return this._state;
  }

  private setState(s: ConnectionState) {
    this._state = s;
    for (const cb of this.stateListeners) {
      try { cb(s); } catch { /* ignore */ }
    }
  }

  onState(cb: (s: ConnectionState) => void): () => void {
    this.stateListeners.add(cb);
    // Fire immediately with current state
    try { cb(this._state); } catch { /* ignore */ }
    return () => { this.stateListeners.delete(cb); };
  }

  // ── Message subscription ──────────────────────────────────────────────

  onMessage(cb: (msg: RoomMessage) => void): () => void {
    this.messageListeners.add(cb);
    return () => { this.messageListeners.delete(cb); };
  }

  private dispatch(msg: RoomMessage) {
    for (const cb of this.messageListeners) {
      try { cb(msg); } catch { /* ignore */ }
    }
  }

  // ── Connection ────────────────────────────────────────────────────────

  async connect(board: string, runId: string): Promise<void> {
    if (this._state === "open" || this._state === "connecting") return;
    this.setState("connecting");

    this._roomId = `${board}/${runId}`;

    // Auth: same pattern as GatewayClient
    let authParamName: string;
    let authParamValue: string;
    if (window.__HERMES_AUTH_REQUIRED__) {
      const { ticket } = await getWsTicket();
      authParamName = "ticket";
      authParamValue = ticket;
    } else {
      authParamName = "token";
      authParamValue = window.__HERMES_SESSION_TOKEN__ ?? "";
      if (!authParamValue) {
        this.setState("error");
        throw new Error(
          "Session token not available — page must be served by the Hermes dashboard",
        );
      }
    }

    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${scheme}//${location.host}${HERMES_BASE_PATH}/ws/room/${encodeURIComponent(board)}/${encodeURIComponent(runId)}?${authParamName}=${encodeURIComponent(authParamValue)}`,
    );
    this.ws = ws;

    ws.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse(ev.data) as RoomMessage;
        this.dispatch(msg);
      } catch {
        /* malformed frame — ignore */
      }
    });

    ws.addEventListener("close", () => {
      this.setState("closed");
      this.scheduleReconnect();
    });

    await new Promise<void>((resolve, reject) => {
      const onOpen = () => {
        ws.removeEventListener("error", onError);
        this.setState("open");
        // Request full replay on fresh connect
        this.requestReplay();
        resolve();
      };
      const onError = () => {
        ws.removeEventListener("open", onOpen);
        this.setState("error");
        reject(new Error("Room WebSocket connection failed"));
      };
      ws.addEventListener("open", onOpen, { once: true });
      ws.addEventListener("error", onError, { once: true });
    });
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this._roomId) return;
      const [board, runId] = this._roomId.split("/");
      if (board && runId) {
        this.connect(board, runId).catch(() => {
          // Exponential backoff, capped at 30s
          this._reconnectMs = Math.min(this._reconnectMs * 2, 30000);
          this.scheduleReconnect();
        });
      }
    }, this._reconnectMs);
  }

  close() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._reconnectMs = 1000;
    this.ws?.close();
    this.ws = null;
    this._roomId = "";
    this.setState("idle");
  }

  // ── Outbound messages ─────────────────────────────────────────────────

  sendMessage(text: string) {
    if (!this.ws || this._state !== "open") return;
    this.ws.send(JSON.stringify({ type: "user_msg", text }));
  }

  requestReplay(since?: number) {
    if (!this.ws || this._state !== "open") return;
    this.ws.send(JSON.stringify({ type: "replay", since: since ?? 0 }));
  }
}
