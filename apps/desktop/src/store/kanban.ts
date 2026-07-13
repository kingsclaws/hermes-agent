/**
 * kanban.ts
 *
 * Nanostore state for kanban task tracking.
 * Connects to lex-hermes-plugin's kanban API.
 */

import { atom, computed } from 'nanostores'

import type { KanbanEvent, KanbanTask } from '@/lib/kanban-api'

export const $kanbanTasks = atom<KanbanTask[]>([])
export const $kanbanConnected = atom(false)
export const $kanbanBoard = atom<string | null>(null)
export const $kanbanEvents = atom<KanbanEvent[]>([])

export const $kanbanActiveTasks = computed($kanbanTasks, (tasks) =>
  tasks.filter((t) => ['running', 'ready', 'blocked'].includes(t.status))
)

export const $kanbanActiveCount = computed($kanbanActiveTasks, (tasks) => tasks.length)

export function setKanbanTasks(tasks: KanbanTask[]) {
  $kanbanTasks.set(tasks)
}

export function updateKanbanTask(task: KanbanTask) {
  const current = $kanbanTasks.get()
  const idx = current.findIndex((t) => t.id === task.id)
  if (idx >= 0) {
    const next = [...current]
    next[idx] = task
    $kanbanTasks.set(next)
  } else {
    $kanbanTasks.set([...current, task])
  }
}

export function appendKanbanEvents(events: KanbanEvent[]) {
  const current = $kanbanEvents.get()
  // Keep last 200 events
  const next = [...current, ...events].slice(-200)
  $kanbanEvents.set(next)
}

export function setKanbanConnected(connected: boolean) {
  $kanbanConnected.set(connected)
}

export function setKanbanBoard(board: string | null) {
  $kanbanBoard.set(board)
}
