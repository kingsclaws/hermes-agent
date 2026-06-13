import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  BUILTIN_SHORTCUTS,
  comboMatchesEvent,
  type ShortcutDef,
} from "@/lib/keyboard-shortcuts";

type Handler = () => void;

interface ShortcutBinding {
  shortcut: ShortcutDef;
  handler: Handler;
  priority: number;
}

export interface KeyboardShortcutsContextValue {
  shortcuts: ShortcutDef[];
  register: (id: string, handler: Handler, priority?: number) => () => void;
}

const KeyboardShortcutsContext =
  createContext<KeyboardShortcutsContextValue | null>(null);

export function KeyboardShortcutsProvider({
  children,
}: {
  children: ReactNode;
}) {
  const bindingsRef = useRef<Map<string, ShortcutBinding[]>>(new Map());
  const [, _rerender] = useState(0);
  const forceUpdate = useCallback(() => _rerender((n) => n + 1), []);

  const register = useCallback(
    (id: string, handler: Handler, priority = 0): (() => void) => {
      const def = BUILTIN_SHORTCUTS.find((s) => s.id === id);
      if (!def) {
        return () => {};
      }

      const binding: ShortcutBinding = { shortcut: def, handler, priority };
      const existing = bindingsRef.current.get(id) ?? [];
      existing.push(binding);
      existing.sort((a, b) => b.priority - a.priority);
      bindingsRef.current.set(id, existing);
      forceUpdate();

      return () => {
        const list = bindingsRef.current.get(id);
        if (list) {
          const idx = list.indexOf(binding);
          if (idx >= 0) list.splice(idx, 1);
          if (list.length === 0) bindingsRef.current.delete(id);
        }
        forceUpdate();
      };
    },
    [forceUpdate],
  );

  // Global keydown listener
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't capture when focused in text inputs (except Escape)
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
      const isInput =
        tag === "input" || tag === "textarea" || tag === "select" || (e.target as HTMLElement)?.isContentEditable;
      if (isInput && e.key !== "Escape") return;

      for (const def of BUILTIN_SHORTCUTS) {
        if (comboMatchesEvent(def.combo, e)) {
          const bindings = bindingsRef.current.get(def.id);
          if (bindings && bindings.length > 0) {
            e.preventDefault();
            e.stopPropagation();
            bindings[0].handler();
            return;
          }
        }
      }
    };

    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  const value = useMemo<KeyboardShortcutsContextValue>(
    () => ({ shortcuts: BUILTIN_SHORTCUTS, register }),
    [register],
  );

  return (
    <KeyboardShortcutsContext.Provider value={value}>
      {children}
    </KeyboardShortcutsContext.Provider>
  );
}

export function useKeyboardShortcuts(): KeyboardShortcutsContextValue {
  const ctx = useContext(KeyboardShortcutsContext);
  if (!ctx)
    throw new Error(
      "useKeyboardShortcuts must be used within KeyboardShortcutsProvider",
    );
  return ctx;
}
