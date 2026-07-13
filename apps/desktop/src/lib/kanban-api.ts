/**
 * kanban-api.ts
 *
 * API client for lex-hermes-plugin kanban endpoints.
 * Calls /api/plugins/kanban/* on the gateway.
 */

export interface KanbanBoard {
  slug: string
  name: string
  description: string
  task_count: number
  created_at: string
}

export interface KanbanTask {
  id: string
  title: string
  status: string
  assignee: string | null
  priority: number
  created_at: string
  updated_at: string
  body?: string
  tags?: string[]
}

export interface KanbanTaskDetail extends KanbanTask {
  events: KanbanEvent[]
  comments: KanbanComment[]
  links: KanbanLink[]
}

export interface KanbanEvent {
  id: number
  task_id: string
  kind: string
  payload: Record<string, unknown> | null
  created_at: string
  run_id?: string
}

export interface KanbanComment {
  id: number
  task_id: string
  author: string
  body: string
  created_at: string
}

export interface KanbanLink {
  parent_id: string
  child_id: string
  kind: string
}

export interface KanbanActiveWorker {
  task_id: string
  title: string
  assignee: string
  pid: number
  started_at: string
}

function headers(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json'
  }
}

export async function fetchBoard(baseUrl: string, token: string, board?: string): Promise<KanbanBoard> {
  const url = new URL('/api/plugins/kanban/board', baseUrl)
  if (board) url.searchParams.set('board', board)

  const res = await fetch(url.toString(), { headers: headers(token) })
  if (!res.ok) throw new Error(`Failed to fetch board: ${res.status}`)
  return res.json()
}

export async function fetchTasks(baseUrl: string, token: string, board?: string): Promise<KanbanTask[]> {
  const url = new URL('/api/plugins/kanban/board', baseUrl)
  if (board) url.searchParams.set('board', board)

  const res = await fetch(url.toString(), { headers: headers(token) })
  if (!res.ok) throw new Error(`Failed to fetch tasks: ${res.status}`)
  const data = await res.json()
  return data.tasks || []
}

export async function fetchTaskDetail(
  baseUrl: string,
  token: string,
  taskId: string,
  board?: string
): Promise<KanbanTaskDetail> {
  const url = new URL(`/api/plugins/kanban/tasks/${encodeURIComponent(taskId)}`, baseUrl)
  if (board) url.searchParams.set('board', board)

  const res = await fetch(url.toString(), { headers: headers(token) })
  if (!res.ok) throw new Error(`Failed to fetch task: ${res.status}`)
  return res.json()
}

export async function fetchActiveWorkers(
  baseUrl: string,
  token: string,
  board?: string
): Promise<KanbanActiveWorker[]> {
  const url = new URL('/api/plugins/kanban/workers/active', baseUrl)
  if (board) url.searchParams.set('board', board)

  const res = await fetch(url.toString(), { headers: headers(token) })
  if (!res.ok) throw new Error(`Failed to fetch workers: ${res.status}`)
  const data = await res.json()
  return data.workers || []
}

export function connectKanbanEvents(
  baseUrl: string,
  token: string,
  onEvent: (events: KanbanEvent[]) => void,
  board?: string
): WebSocket {
  const url = new URL('/api/plugins/kanban/events', baseUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('token', token)
  if (board) url.searchParams.set('board', board)

  const ws = new WebSocket(url.toString())

  ws.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data)
      if (data.events) onEvent(data.events)
    } catch {
      // ignore parse errors
    }
  }

  return ws
}
