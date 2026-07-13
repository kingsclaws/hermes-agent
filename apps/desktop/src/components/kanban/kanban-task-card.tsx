/**
 * kanban-task-card.tsx
 *
 * Single kanban task card showing title, status, assignee, and progress.
 */

import { cn } from '@/lib/utils'

import type { KanbanTask } from '@/lib/kanban-api'

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-500',
  ready: 'bg-yellow-500',
  done: 'bg-green-500',
  blocked: 'bg-red-500',
  todo: 'bg-gray-400',
  archived: 'bg-gray-300'
}

const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  ready: 'Ready',
  done: 'Done',
  blocked: 'Blocked',
  todo: 'Todo',
  archived: 'Archived'
}

interface KanbanTaskCardProps {
  task: KanbanTask
  onClick?: (taskId: string) => void
}

export function KanbanTaskCard({ task, onClick }: KanbanTaskCardProps) {
  const statusColor = STATUS_COLORS[task.status] || 'bg-gray-400'
  const statusLabel = STATUS_LABELS[task.status] || task.status

  return (
    <button
      className={cn(
        'flex w-full flex-col gap-1 rounded-md border border-(--ui-stroke-tertiary) p-2 text-left',
        'hover:bg-(--ui-control-hover-background) transition-colors'
      )}
      onClick={() => onClick?.(task.id)}
      type="button"
    >
      <div className="flex items-center gap-2">
        <span className={cn('size-2 shrink-0 rounded-full', statusColor)} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{task.title}</span>
      </div>
      <div className="flex items-center gap-2 pl-4">
        <span className="text-[0.625rem] text-(--ui-text-tertiary)">{statusLabel}</span>
        {task.assignee && (
          <>
            <span className="text-[0.625rem] text-(--ui-text-tertiary)">·</span>
            <span className="text-[0.625rem] text-(--ui-text-tertiary)">@{task.assignee}</span>
          </>
        )}
      </div>
    </button>
  )
}
