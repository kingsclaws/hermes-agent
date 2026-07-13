/**
 * use-kanban.ts
 *
 * React hook for kanban state. Subscribes to store atoms and
 * manages WebSocket connection to kanban events.
 */

import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { connectKanbanEvents, fetchTasks } from '@/lib/kanban-api'
import {
  $kanbanActiveCount,
  $kanbanActiveTasks,
  $kanbanBoard,
  $kanbanConnected,
  $kanbanEvents,
  $kanbanTasks,
  appendKanbanEvents,
  setKanbanConnected,
  setKanbanTasks,
  updateKanbanTask
} from '@/store/kanban'

export function useKanban() {
  const tasks = useStore($kanbanTasks)
  const activeTasks = useStore($kanbanActiveTasks)
  const activeCount = useStore($kanbanActiveCount)
  const connected = useStore($kanbanConnected)
  const board = useStore($kanbanBoard)
  const events = useStore($kanbanEvents)

  return { tasks, activeTasks, activeCount, connected, board, events }
}

export function useKanbanConnection(baseUrl: string | null, token: string | null) {
  const wsRef = useRef<WebSocket | null>(null)

  const loadTasks = useCallback(async () => {
    if (!baseUrl || !token) return
    try {
      const tasks = await fetchTasks(baseUrl, token)
      setKanbanTasks(tasks)
    } catch {
      // silent
    }
  }, [baseUrl, token])

  useEffect(() => {
    if (!baseUrl || !token) {
      setKanbanConnected(false)
      return
    }

    // Initial load
    loadTasks()

    // Connect WebSocket for live updates
    const ws = connectKanbanEvents(
      baseUrl,
      token,
      (newEvents) => {
        appendKanbanEvents(newEvents)
        // Refresh tasks on any event
        loadTasks()
      }
    )

    ws.onopen = () => setKanbanConnected(true)
    ws.onclose = () => setKanbanConnected(false)
    ws.onerror = () => setKanbanConnected(false)

    wsRef.current = ws

    return () => {
      ws.close()
      wsRef.current = null
      setKanbanConnected(false)
    }
  }, [baseUrl, token, loadTasks])

  return { loadTasks }
}
