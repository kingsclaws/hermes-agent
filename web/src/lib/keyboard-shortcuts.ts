export interface ShortcutDef {
  id: string;
  label: string;
  /** Human-readable key combo, e.g. "Ctrl+S" */
  displayKey: string;
  /** Normalized combo for matching: "ctrl+s", "cmd+k" etc. */
  combo: string;
  category: string;
  scope: "global" | "chat" | "files" | "dashboard";
}

const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

const mod = isMac ? "Cmd" : "Ctrl";

export const BUILTIN_SHORTCUTS: ShortcutDef[] = [
  {
    id: "palette",
    combo: "mod+k",
    displayKey: `${mod}+K`,
    label: "Command Palette",
    category: "General",
    scope: "global",
  },
  {
    id: "shortcuts-dialog",
    combo: "mod+shift+/",
    displayKey: `${mod}+?`,
    label: "Keyboard Shortcuts",
    category: "General",
    scope: "global",
  },
  {
    id: "toggle-sidebar",
    combo: "mod+b",
    displayKey: `${mod}+B`,
    label: "Toggle Sidebar",
    category: "General",
    scope: "global",
  },
  {
    id: "toggle-files",
    combo: "mod+shift+e",
    displayKey: `${mod}+Shift+E`,
    label: "Toggle File Explorer",
    category: "General",
    scope: "global",
  },
  {
    id: "close-panel",
    combo: "mod+w",
    displayKey: `${mod}+W`,
    label: "Close Active Panel",
    category: "Panel",
    scope: "global",
  },
  {
    id: "escape",
    combo: "escape",
    displayKey: "Esc",
    label: "Close Panel / Dialog",
    category: "General",
    scope: "global",
  },
  {
    id: "send-message",
    combo: "mod+enter",
    displayKey: `${mod}+Enter`,
    label: "Send Message",
    category: "Chat",
    scope: "chat",
  },
  {
    id: "next-tab",
    combo: "mod+shift+]",
    displayKey: `${mod}+Tab`,
    label: "Next Chat Tab",
    category: "Chat",
    scope: "chat",
  },
  {
    id: "prev-tab",
    combo: "mod+shift+[",
    displayKey: `${mod}+Shift+Tab`,
    label: "Previous Chat Tab",
    category: "Chat",
    scope: "chat",
  },
  {
    id: "save-file",
    combo: "mod+s",
    displayKey: `${mod}+S`,
    label: "Save Active Document",
    category: "Files",
    scope: "files",
  },
  {
    id: "rename-file",
    combo: "f2",
    displayKey: "F2",
    label: "Rename File",
    category: "Files",
    scope: "files",
  },
  {
    id: "delete-file",
    combo: "delete",
    displayKey: "Delete",
    label: "Delete File",
    category: "Files",
    scope: "files",
  },
];

export function parseKeyCombo(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey) parts.push("mod");
  if (e.ctrlKey) parts.push("mod");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  const key = e.key.toLowerCase();
  if (!["control", "alt", "shift", "meta"].includes(key)) {
    parts.push(key === " " ? "space" : key);
  }
  return parts.join("+");
}

export function comboMatchesEvent(combo: string, e: KeyboardEvent): boolean {
  const eventCombo = parseKeyCombo(e);
  // Replace "mod" with "ctrl" in both for normalized comparison
  const normalize = (s: string) => s.replace(/mod/g, "ctrl");
  return normalize(combo) === normalize(eventCombo);
}
