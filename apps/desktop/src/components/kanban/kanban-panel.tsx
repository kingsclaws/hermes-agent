/**
 * kanban-panel.tsx
 *
 * Main kanban panel showing task list grouped by status.
 */

import { cn } from '@/lib/utils'

import type { KanbanTask } from '@/lib/kanban-api'

import { KanbanTaskCard } from './kanban-task-card'

interface KanbanPanelProps {
  tasks: KanbanTask[]
  connected: boolean
  onClose: () => void
  onTaskClick?: (taskId: string) => void
}

function groupByStatus(tasks: KanbanTask[]): Record<string, KanbanTask[]> {
  const groups: Record<string, KanbanTask[]> = {}
  for (const task of tasks) {
    const status = task.status || 'unknown'
    if (!groups[status]) groups[status] = []
    groups[status].push(task)
  }
  return groups
}

const STATUS_ORDER = ['running', 'ready', 'blocked', 'todo', 'done', 'archived']

export function KanbanPanel({ tasks, connected, onClose, onTaskClick }: KanbanPanelProps) {
  const groups = groupByStatus(tasks)
  const sortedStatuses = STATUS_ORDER.filter((s) => groups[s]?.length)

  return (
    <div
      className={cn(
        'absolute bottom-14 right-4 z-50 flex h-96 w-80 flex-col',
        'rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-background) shadow-lg'
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium">Kanban Tasks</span>
          <span
            className={cn(
              'size-1.5 rounded-full',
              connected ? 'bg-green-500' : 'bg-gray-400'
            )}
          />
        </div>
        <button
          className="text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)"
          onClick={onClose}
          type="button"
        >
          <svg className="size-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} />
          </svg>
        </button>
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto p-2">
        {tasks.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-(--ui-text-tertiary)">
            No tasks
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {sortedStatuses.map((status) => (
              <div key={status}>
                <div className="mb-1 text-[0.625rem] font-medium uppercase text-(--ui-text-tertiary)">
                  {status} ({groups[status].length})
                </div>
                <div className="flex flex-col gap-1">
                  {groups[status].map((task) => (
                    <KanbanTaskCard key={task.id} task={task} onClick={onTaskClick} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
