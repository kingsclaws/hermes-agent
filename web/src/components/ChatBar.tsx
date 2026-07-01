/**
 * ChatBar — Desktop-grade composer component.
 *
 * Mirrors Desktop's `apps/desktop/src/app/chat/composer/index.tsx` structure:
 * centered input with Desktop input chrome, send/cancel buttons, status.
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { Send, Square, Paperclip } from "lucide-react";
import type { ConnectionState } from "@/lib/gatewayClient";
import { useRef, useState } from "react";

export interface ChatBarProps {
  input: string;
  setInput: (v: string) => void;
  submit: (text: string) => Promise<void>;
  cancel: () => void;
  running: boolean;
  uploading: boolean;
  sessionId: string | null;
  conn: ConnectionState;
}

export function ChatBar({
  input,
  setInput,
  submit,
  cancel,
  running,
  uploading,
  sessionId,
  conn,
}: ChatBarProps) {
  const disabled = !sessionId || conn !== "open";
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="shrink-0 px-4 pb-3"
      style={{
        background: "linear-gradient(to top, var(--dt-background) 60%, transparent)",
      }}
    >
      {/* Composer surface — Desktop input chrome */}
      <div className="mx-auto w-full"
        style={{ maxWidth: "48.75rem" }}
      >
        <form
          onSubmit={(e) => { e.preventDefault(); if (!disabled && input.trim()) submit(input); }}
          className="flex items-end gap-2 rounded-lg border px-3 py-2 transition-colors duration-200"
          style={{
            background: "var(--dt-panel-strong)",
            borderColor: "var(--ui-stroke-secondary)",
            boxShadow: "inset 0 1px 1px rgba(0,0,0,0.3)",
          }}
          onFocusCapture={() => {}}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!disabled && input.trim()) submit(input);
              }
            }}
            disabled={disabled}
            rows={1}
            placeholder="Message Lex-Hermes..."
            className="min-h-[1.625rem] max-h-[9.375rem] flex-1 resize-none bg-transparent text-[0.8125rem] leading-[1.5] outline-none placeholder:text-[var(--text-low)]"
            style={{
              color: "var(--text-high)",
              fontFamily: "var(--theme-font-sans)",
            }}
          />

          <div className="flex shrink-0 items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={() => {}}
            />
            <Button
              type="button"
              ghost
              size="icon"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || uploading}
              title="Attach files"
              className="h-7 w-7 rounded-md"
              style={{ color: "var(--text-low)" }}
            >
              <Paperclip className="h-3.5 w-3.5" />
            </Button>

            {running ? (
              <Button
                type="button"
                ghost
                size="icon"
                onClick={cancel}
                className="h-7 w-7 rounded-md"
                style={{ color: "var(--dt-destructive)" }}
                title="Stop"
              >
                <Square className="h-3.5 w-3.5" fill="currentColor" />
              </Button>
            ) : (
              <Button
                type="submit"
                ghost
                size="icon"
                disabled={disabled || !input.trim()}
                className="h-7 w-7 rounded-md"
                style={{
                  color: input.trim() && !disabled ? "var(--theme-primary)" : "var(--text-low)",
                  opacity: input.trim() && !disabled ? 1 : 0.5,
                }}
                title="Send (Enter)"
              >
                <Send className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
