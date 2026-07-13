/**
 * kanban-floating-button.tsx
 *
 * Floating button showing active kanban task count.
 * Click to toggle the kanban panel.
 */

import { cn } from '@/lib/utils'

interface KanbanFloatingButtonProps {
  count: number
  connected: boolean
  active: boolean
  onClick: () => void
}

export function KanbanFloatingButton({ count, connected, active, onClick }: KanbanFloatingButtonProps) {
  return (
    <button
      className={cn(
        'absolute bottom-4 right-4 z-50 flex items-center gap-1.5 rounded-full px-3 py-1.5',
        'border border-(--ui-stroke-tertiary) shadow-md transition-all',
        active
          ? 'bg-primary text-primary-foreground'
          : 'bg-(--ui-background) text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)'
      )}
      onClick={onClick}
      type="button"
    >
      {/* Kanban icon */}
      <svg className="size-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path
          d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
        />
      </svg>

      {/* Count badge */}
      {count > 0 && (
        <span className="text-xs font-medium">{count}</span>
      )}

      {/* Connection indicator */}
      <span
        className={cn(
          'size-1.5 rounded-full',
          connected ? 'bg-green-400' : 'bg-gray-400'
        )}
      />
    </button>
  )
}
