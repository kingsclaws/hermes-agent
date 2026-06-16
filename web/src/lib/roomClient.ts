/** Lightweight WebSocket client for the legal swarm chat room protocol.

The room protocol is simple JSON (not JSON-RPC):

Inbound (server → client):  ``RoomMessage`` objects
Outbound (client → server):
  ``{type: "user_msg", text: "..."}``  — broadcast a user message
  ``{type: "replay"}``                — request full message history

For standalone chatrooms (no kanban):
  ``{type: "join", username, role}``   — register in the room
  ``{type: "message", content}``       — send a chat message

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
  /** When present, contains replay messages from server */
  _replay?: RoomMessage[];
}

export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";

// ---------------------------------------------------------------------------
// ChatroomClient
// ---------------------------------------------------------------------------

type ProtocolType = "room" | "chatroom";

export class ChatroomClient {
  private ws: WebSocket | null = null;
  private _state: ConnectionState = "idle";
  private stateListeners = new Set<(s: ConnectionState) => void>();
  private messageListeners = new Set<(msg: RoomMessage) => void>();
  private _roomId = "";
  private _protocol: ProtocolType = "room";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _reconnectMs = 1000;
  private _username = "";

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
    try { cb(this._state); } catch { /* ignore */ }
    return () => { this.stateListeners.delete(cb); };
  }

  // ── Message subscription ──────────────────────────────────────────────

  onMessage(cb: (msg: RoomMessage) => void): () => void {
    this.messageListeners.add(cb);
    return () => { this.messageListeners.delete(cb); };
  }

  private dispatch(msg: RoomMessage) {
    // If this is a replay envelope, dispatch each contained message
    if (msg._replay && Array.isArray(msg._replay)) {
      for (const m of msg._replay) {
        for (const cb of this.messageListeners) {
          try { cb(m); } catch { /* ignore */ }
        }
      }
      return;
    }
    for (const cb of this.messageListeners) {
      try { cb(msg); } catch { /* ignore */ }
    }
  }

  // ── Auth helper ───────────────────────────────────────────────────────

  private authParams(): { name: string; value: string } {
    if (window.__HERMES_AUTH_REQUIRED__) {
      return { name: "ticket", value: "" };
    }
    return {
      name: "token",
      value: window.__HERMES_SESSION_TOKEN__ ?? "",
    };
  }

  private async resolveAuth(): Promise<{ name: string; value: string }> {
    if (window.__HERMES_AUTH_REQUIRED__) {
      const { ticket } = await getWsTicket();
      return { name: "ticket", value: ticket };
    }
    const token = window.__HERMES_SESSION_TOKEN__ ?? "";
    if (!token) {
      throw new Error(
        "Session token not available — page must be served by the Hermes dashboard",
      );
    }
    return { name: "token", value: token };
  }

  // ── Connection: room protocol (kanban-linked) ─────────────────────────

  async connect(board: string, runId: string): Promise<void> {
    if (this._state === "open" || this._state === "connecting") return;
    this._protocol = "room";
    this.setState("connecting");
    this._roomId = `${board}/${runId}`;

    const { name, value } = await this.resolveAuth();
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${scheme}//${location.host}${HERMES_BASE_PATH}/ws/room/${encodeURIComponent(board)}/${encodeURIComponent(runId)}?${name}=${encodeURIComponent(value)}`;
    await this._openWs(url);
  }

  // ── Connection: chatroom protocol (standalone, no kanban) ─────────────

  async connectChatroom(roomId: string, username?: string): Promise<void> {
    if (this._state === "open" || this._state === "connecting") return;
    this._protocol = "chatroom";
    this.setState("connecting");
    this._roomId = roomId;
    this._username = username || `user-${Math.random().toString(36).slice(2, 8)}`;

    const { name, value } = await this.resolveAuth();
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${scheme}//${location.host}${HERMES_BASE_PATH}/ws/chatroom/${encodeURIComponent(roomId)}?${name}=${encodeURIComponent(value)}`;
    await this._openWs(url);
  }

  private async _openWs(url: string): Promise<void> {
    const ws = new WebSocket(url);
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
        // Register in chatroom or request replay
        if (this._protocol === "chatroom") {
          ws.send(JSON.stringify({
            type: "join",
            username: this._username,
            role: "user",
          }));
        } else {
          this.requestReplay();
        }
        resolve();
      };
      const onError = () => {
        ws.removeEventListener("open", onOpen);
        this.setState("error");
        reject(new Error("Chatroom WebSocket connection failed"));
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
      const reconnect = this._protocol === "chatroom"
        ? this.connectChatroom(this._roomId, this._username)
        : (() => {
            const [board, runId] = this._roomId.split("/");
            if (board && runId) return this.connect(board, runId);
            return Promise.resolve();
          })();
      reconnect.catch(() => {
        this._reconnectMs = Math.min(this._reconnectMs * 2, 30000);
        this.scheduleReconnect();
      });
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
    this._username = "";
    this.setState("idle");
  }

  // ── Outbound messages ─────────────────────────────────────────────────

  sendMessage(text: string) {
    if (!this.ws || this._state !== "open") return;
    if (this._protocol === "chatroom") {
      this.ws.send(JSON.stringify({ type: "message", content: text }));
    } else {
      this.ws.send(JSON.stringify({ type: "user_msg", text }));
    }
  }

  requestReplay(since?: number) {
    if (!this.ws || this._state !== "open") return;
    this.ws.send(JSON.stringify({ type: "replay", since: since ?? 0 }));
  }
}

// Backward-compatible alias
export { ChatroomClient as RoomClient };
