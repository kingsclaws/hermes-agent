import { useMemo } from "react";
import { useKeyboardShortcuts } from "@/contexts/KeyboardShortcutsContext";

export function KeyboardShortcutsDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { shortcuts } = useKeyboardShortcuts();

  const categories = useMemo(() => {
    const map = new Map<string, typeof shortcuts>();
    for (const s of shortcuts) {
      const list = map.get(s.category) ?? [];
      list.push(s);
      map.set(s.category, list);
    }
    return [...map.entries()];
  }, [shortcuts]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-xl rounded-lg border border-primary/20 bg-background-base shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 border-b border-current/10 px-4 py-3">
          <h2 className="text-sm font-bold tracking-wide">Keyboard Shortcuts</h2>
          <div className="flex-1" />
          <kbd className="rounded border border-current/20 px-1.5 py-0.5 text-[0.6rem] text-muted-foreground font-mono">
            esc
          </kbd>
        </div>

        <div className="max-h-96 overflow-y-auto p-1">
          {categories.map(([cat, items]) => (
            <div key={cat}>
              <div className="px-3 pt-3 pb-1 text-[0.65rem] uppercase tracking-wider text-muted-foreground font-bold">
                {cat}
              </div>
              {items.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between gap-4 rounded px-3 py-1.5 text-sm"
                >
                  <span>{s.label}</span>
                  <kbd className="rounded border border-current/20 px-1.5 py-0.5 text-[0.65rem] text-muted-foreground font-mono shrink-0">
                    {s.displayKey}
                  </kbd>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
