# Desktop Kanban Integration TODO

## Context

When Desktop connects to lex-hermes remotely, it needs kanban/swarm visualization.
Our WebUI already has these features; they need to be ported to Desktop React code.

## TODO

### Phase 1: Right-Sidebar Kanban Tab
- [ ] Add Kanban tab to right-sidebar (`apps/desktop/src/app/right-sidebar/`)
- [ ] Create `CompactKanbanBoard.tsx` — port from `web/src/components/CompactKanbanPanel.tsx`
- [ ] Wire into right-sidebar tab system (alongside Files, Review, Terminal)
- [ ] Subscribe to kanban events via WebSocket (`kanban.*` event prefix)
- [ ] Show task columns: Ready, Running, Done, Blocked, Todo

### Phase 2: Chat Inline Swarm View
- [ ] Create `SwarmInlineView.tsx` — port from `web/src/components/SwarmInlineView.tsx`
- [ ] Render inside chat message stream when swarm is active
- [ ] Show subagent columns with profile, goal, streaming output
- [ ] Auto-expand on swarm start, auto-collapse on completion

### Phase 3: Full-Page Kanban Board
- [ ] Add `/kanban` route
- [ ] Create `KanbanBoardPage.tsx` — port from `web/src/pages/SwarmBoardPage.tsx`
- [ ] Board view (columns) + List view (flat)
- [ ] Progress bar, auto-refresh every 15s
- [ ] Launcher UI for starting new runs

### Phase 4: Kanban Task Overlay in Chat
- [ ] Create `KanbanTaskOverlay.tsx` — floating panel triggered from chat
- [ ] Show pending/assigned tasks for current user
- [ ] Quick actions: claim, complete, handoff
- [ ] Command palette integration (`Ctrl+K → "kanban"`)

### Phase 5: Legal Workflow Integration
- [ ] Port `LegalWorkflowPanel.tsx` for document workflows
- [ ] SVG dependency graph for workflow steps
- [ ] Step editing: title, role, type, depends_on, instructions
- [ ] Workflow actions: register, read, plan, draft, review, proofread, deliver

## Key Files to Create/Modify

| File | Purpose |
|------|---------|
| `apps/desktop/src/app/right-sidebar/kanban/` | Kanban tab in right sidebar |
| `apps/desktop/src/components/chat/kanban-overlay.tsx` | Floating kanban overlay |
| `apps/desktop/src/app/kanban/` | Full-page kanban board |
| `apps/desktop/src/components/chat/swarm-inline.tsx` | In-chat swarm view |
| `apps/desktop/src/store/kanban.ts` | Kanban state (mirrors lex-hermes) |

## API Endpoints (lex-hermes provides)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/kanban/swarm/runs` | List active swarm runs |
| `GET /api/kanban/swarm/runs/{id}` | Get run details with tasks |
| `POST /api/kanban/swarm/runs/{id}/tasks/{task_id}` | Update task status |
| `GET /api/projects/{id}/files` | Browse project files |
| `GET /api/projects/{id}/workflows` | List project workflows |
