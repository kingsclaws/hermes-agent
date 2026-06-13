import {
  Panel,
  Group,
  Separator,
  type PanelProps,
  type GroupProps,
} from "react-resizable-panels";
import { cn } from "@/lib/utils";

export function ResizablePanelGroup({
  className,
  orientation = "horizontal",
  ...rest
}: GroupProps & { orientation?: "horizontal" | "vertical" }) {
  return (
    <Group
      orientation={orientation}
      className={cn("min-h-0 min-w-0", className)}
      {...rest}
    />
  );
}

export function ResizablePanel({
  className,
  minSize = 10,
  ...rest
}: PanelProps) {
  return (
    <Panel
      className={cn("min-h-0 min-w-0", className)}
      minSize={minSize}
      {...rest}
    />
  );
}

export function ResizableHandle({
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Separator
      className={cn(
        "group relative w-2 shrink-0",
        "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-current/15 after:transition-colors",
        "hover:after:bg-current/30",
        "data-[resize-handle-active]:after:bg-primary/60",
        className,
      )}
      {...rest}
    />
  );
}
