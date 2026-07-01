/**
 * ChatView — Desktop-grade chat view component.
 *
 * Mirrors Desktop's `apps/desktop/src/app/chat/index.tsx` structure:
 *   ChatHeader → Thread (ChatMessageList) → ChatBar (Composer)
 *
 * Wraps the existing NativeChatSurface with Desktop-equivalent
 * layout, styling, and component decomposition.
 */

import { ChatMessageList } from "@/components/ChatMessageList";
import { ChatBar } from "@/components/ChatBar";
import type { ConnectionState } from "@/lib/gatewayClient";

export interface ChatViewProps {
  messages: any[];
  thinkingBlocks: any;
  tools: any[];
  subagents: any[];
  swarmState: any;
  running: boolean;
  assistantIdRef: { current: string | null };
  sessionId: string | null;
  projectContext?: { id: string; name: string; cwd?: string } | null;
  conn: ConnectionState;
  error: string | null;
  input: string;
  setInput: (v: string) => void;
  submit: (text: string) => Promise<void>;
  cancel: () => void;
  uploading: boolean;
  hasQueued: boolean;
  queuedPrompt: string;
  cancelQueue: () => void;
}

export function ChatView({
  messages,
  thinkingBlocks,
  tools,
  subagents,
  swarmState,
  running,
  assistantIdRef,
  sessionId,
  projectContext,
  conn,
  error,
  input,
  setInput,
  submit,
  cancel,
  uploading,
  hasQueued,
  queuedPrompt,
  cancelQueue,
}: ChatViewProps) {
  return (
    <div className="relative isolate flex h-full min-w-0 flex-col overflow-hidden"
      style={{ background: "var(--dt-background)" }}
    >
      {/* ChatHeader — minimal session title bar */}
      <ChatHeader
        sessionId={sessionId}
        projectContext={projectContext}
        conn={conn}
      />

      {/* Thread — message list */}
      <div className="min-h-0 flex-1 overflow-hidden"
        style={{ contain: "layout paint" }}
      >
        <ChatMessageList
          messages={messages}
          thinkingBlocks={thinkingBlocks}
          tools={tools}
          subagents={subagents}
          swarmState={swarmState}
          running={running}
          assistantIdRef={assistantIdRef}
          className="h-full"
        />

        {/* Error overlay */}
        {error && (
          <div className="absolute bottom-0 left-0 right-0 px-3 py-2 text-xs"
            style={{
              background: "color-mix(in srgb, var(--dt-destructive) 8%, transparent)",
              color: "var(--dt-destructive)",
              borderTop: "1px solid var(--dt-destructive)",
            }}
          >
            <span className="font-semibold">Error</span>
            <span className="ml-2 opacity-80">{error}</span>
          </div>
        )}

        {/* Queue banner */}
        {hasQueued && (
          <div className="absolute bottom-0 left-0 right-0 px-3 py-2 text-xs"
            style={{
              background: "color-mix(in srgb, #f59e0b 10%, transparent)",
              color: "#f59e0b",
              borderTop: "1px solid rgba(245,158,11,0.3)",
            }}
          >
            <span>Queued: {queuedPrompt.slice(0, 80)}</span>
            <button
              onClick={cancelQueue}
              className="ml-2 rounded px-1.5 py-0.5 text-[0.65rem] hover:bg-amber-500/10"
            >cancel</button>
          </div>
        )}
      </div>

      {/* ChatBar — composer at bottom */}
      <ChatBar
        input={input}
        setInput={setInput}
        submit={submit}
        cancel={cancel}
        running={running}
        uploading={uploading}
        sessionId={sessionId}
        conn={conn}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChatHeader
// ---------------------------------------------------------------------------

function ChatHeader({
  sessionId,
  projectContext,
  conn,
}: {
  sessionId: string | null;
  projectContext?: { id: string; name: string; cwd?: string } | null;
  conn: ConnectionState;
}) {
  return (
    <header className="flex h-9 shrink-0 items-center gap-3 border-b px-3"
      style={{
        borderColor: "var(--ui-stroke-tertiary)",
        color: "var(--text-high)",
      }}
    >
      <span className="text-[0.7rem] font-semibold uppercase tracking-[0.07em]"
        style={{ color: "var(--text-medium)" }}
      >
        {sessionId ? "Session" : "New session"}
      </span>

      {projectContext && (
        <span className="truncate rounded px-2 py-0.5 text-[0.65rem]"
          style={{
            background: "var(--accent-fill-1)",
            color: "var(--theme-primary)",
          }}
        >
          {projectContext.name}
        </span>
      )}

      <div className="flex-1" />

      <span className="text-[0.65rem]"
        style={{
          color: conn === "open" ? "var(--color-success)" : "var(--text-low)",
        }}
      >
        {conn}
      </span>
    </header>
  );
}
