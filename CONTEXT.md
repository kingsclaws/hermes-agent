# Lex-Hermes Legal Harness

Lex-Hermes is a legal document harness for orchestrating, reviewing, and delivering lawyer-style Word document work through visible multi-agent workflows.

## Language

**Workflow Run**:
The user-visible legal matter workflow that owns the overall plan, step order, status, and reviewable result for a document task.
_Avoid_: Canvas task, chat task, ad hoc session

**Kanban Execution Layer**:
The mandatory backend execution layer for workflow steps, where agent work is claimed, progressed, handed off, approved, or rejected.
_Avoid_: Optional board, side queue, parallel tracker

**Workflow Fact Layer**:
The canonical evidence and fact record produced during execution, including project facts, handoff evidence, verification records, and scorecard-relevant outputs.
_Avoid_: Chat summary, informal note, final prose only

**Autonomous Kanban Entry**:
The behavior where a CLI or TUI agent starts and advances legal document work through the kanban execution layer without requiring the WebUI to launch the workflow.
_Avoid_: WebUI-only workflow, manual board setup, decorative kanban

**Workflow Viewer**:
The WebUI role for observing, inspecting, editing, and intervening in workflow state without being the required execution path.
_Avoid_: Primary executor, required launcher
