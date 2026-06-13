import { useEffect, useRef, useCallback, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export interface ContextMenuPosition {
  x: number;
  y: number;
}

export function ContextMenu({
  position,
  onClose,
  children,
  className,
}: {
  position: ContextMenuPosition;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // Clamp to viewport
  const clampedPosition = useRef<ContextMenuPosition>(position);
  const hasClamped = useRef(false);

  if (!hasClamped.current) {
    hasClamped.current = true;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const estimated = { w: 200, h: 200 };
    clampedPosition.current = {
      x: Math.min(position.x, vw - estimated.w - 8),
      y: Math.min(position.y, vh - estimated.h - 8),
    };
  }

  // Re-clamp after mount when we know real size
  useEffect(() => {
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let { x, y } = position;

      if (x + rect.width > vw - 8) x = vw - rect.width - 8;
      if (y + rect.height > vh - 8) y = vh - rect.height - 8;
      if (x < 4) x = 4;
      if (y < 4) y = 4;

      ref.current.style.left = `${x}px`;
      ref.current.style.top = `${y}px`;
    }
  }, [position]);

  const handleClickOutside = useCallback(
    (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    // Delay to avoid the same click that opened it closing it
    const id = setTimeout(() => {
      document.addEventListener("mousedown", handleClickOutside, true);
    }, 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", handleClickOutside, true);
    };
  }, [handleClickOutside]);

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Close on scroll
  useEffect(() => {
    const onScroll = () => onClose();
    window.addEventListener("scroll", onScroll, true);
    return () => window.removeEventListener("scroll", onScroll, true);
  }, [onClose]);

  return createPortal(
    <div
      ref={ref}
      role="menu"
      className={cn(
        "fixed z-[100] min-w-[160px] rounded-md border border-current/15",
        "bg-background-base/95 backdrop-blur-sm shadow-xl",
        "animate-in fade-in zoom-in-95 py-1",
        className,
      )}
      style={{
        left: clampedPosition.current.x,
        top: clampedPosition.current.y,
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
