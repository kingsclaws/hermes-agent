import { cn } from "@/lib/utils";

export interface ContextMenuItemDef {
  label: string;
  icon?: React.ReactNode;
  shortcut?: string;
  disabled?: boolean;
  variant?: "default" | "destructive";
  separator?: boolean;
  onClick: () => void;
}

export function ContextMenuItem({
  label,
  icon,
  shortcut,
  disabled,
  variant = "default",
  onClick,
}: ContextMenuItemDef) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[0.8rem] transition-colors",
        disabled && "opacity-40 cursor-not-allowed",
        variant === "destructive"
          ? "text-destructive hover:bg-destructive/10"
          : "text-foreground hover:bg-muted/10",
      )}
    >
      {icon && <span className="h-4 w-4 shrink-0 flex items-center justify-center text-muted-foreground">{icon}</span>}
      <span className="flex-1 min-w-0 truncate">{label}</span>
      {shortcut && (
        <kbd className="rounded border border-current/15 px-1 py-0.5 text-[0.6rem] text-muted-foreground font-mono shrink-0 ml-3">
          {shortcut}
        </kbd>
      )}
    </button>
  );
}

export function ContextMenuSeparator() {
  return <div className="my-1 h-px bg-current/10" role="separator" />;
}
