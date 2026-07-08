# Lex-Hermes Agent — Project Context

## Branch Strategy

- `lex-hermes` = production branch (mirror of `lex-hermes:latest` Docker image)
- `lex-hermes-plugin` = development branch (clean upstream + Lex plugin)
- `upstream` remote = `NousResearch/hermes-agent` (official hermes-agent)

**Rule: Every code change must be committed to both branches and baked into `lex-hermes:latest` Docker image.**

## Docker Images

- `localhost:6678/lex-hermes:latest` = production (lex-hermes branch)
- `localhost:6678/lex-hermes:plugin` = plugin branch (lex-hermes-plugin)

**Container:** `lex-hermes` (persistent BTRFS volumes at `/root/.hermes/`, `/root/.config/`, `/root/.local/`, `/workspace/`, `/data/projects/`, `/workingfile/`)

## Profiles (SOULs)

| Profile | File | Role |
|---------|------|------|
| default | `/opt/hermes/SOUL.md` | Coordinator (总协调) |
| lex-master | `/root/.hermes/SOUL.md` | 总入口路由 |
| lex-drafter | `profiles/lex-drafter/SOUL.md` | 文档起草 |
| lex-reviewer-content | `profiles/lex-reviewer-content/SOUL.md` | 内容审阅 |
| lex-reviewer-format | `profiles/lex-reviewer-format/SOUL.md` | 格式审阅 |
| lex-reviewer-ts | `profiles/lex-reviewer-ts/SOUL.md` | TS一致性审阅 |
| lex-reviewer-xref | `profiles/lex-reviewer-xref/SOUL.md` | 交叉引用审阅 |

**Rule: Profile changes must be synced to both the repo (`profiles/`) AND the container (`/root/.hermes/profiles/`), then baked into the image.**

## Key Architecture Decisions

- **WeChat gateway** = lex-master profile (not default)
- **Default gateway** = coordinator (no WeChat)
- **lex-coordinator profile** = DELETED (default IS the coordinator)
- **Session binding** = `coordinator_for` column in sessions table (survives compression)
- **Kanban notifications** = route to project coordinator (not lex-master)
- **lex_master_route(action="report")** = bypass delivery gate
- **lex_preview** = pandoc + XML format injection (font/size/spacing)
- **lex_read** = lightweight by default (show_format=false)

## Workflow (Legal Document Production)

**Complex documents (100+ paragraphs):**
1. 结构扫描 → `lex_read(mode="structure")`
2. Split-read → parallel kanban tasks per section
3. 文本润色确认 → grill-me style Q&A with user
4. 文本润色执行 → precise TC edits
5. 内容注入 → clean inject (only change what's needed)
6. 对比验证 → quicompare original vs edited
7. 校对 → `lex_preview` HTML verification
8. 交付 → md + html + docx

**Simple documents (<50 paragraphs):**
1. Direct TC injection → `insert_text/replace_text/delete_text`
2. 校对 → `lex_preview`
3. 交付

## Tools (Lex-only, via plugin)

Registered via `plugins/lex-legal-tools/__init__.py`:
- `tools/lex_*.py` (lexitool wrappers)
- `tools/legal_*.py` (legal workflow)
- `tools/research_tool.py` (deep research)
- `tools/project_management_tool.py` (project management)
- `tools/kanban_toolset.py` (kanban swarm)

## Skills

Located at `/root/.hermes/skills/` (container) and `.claude/skills/` (Claude Code):
- `deep-research` — research workflow
- `lex-master-handoff` — coordinator handoff protocol
- `hermes-upstream` — upstream sync procedure
