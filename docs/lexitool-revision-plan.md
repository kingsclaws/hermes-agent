# Lexitool Revision Plan

## Goal

Make `lexitool` fit legal document work better:

- Read whole documents as legal material, not just text blobs.
- Edit documents in small, precise, reviewable steps.
- Preserve Word formatting, cross-references, comments, headers/footers, and track changes.
- Support active-session workflows, not just one-off CLI edits.

## What We Learned From Comparable Projects

### `adeu`

Best takeaway: treat DOCX as a semantic document layer, not only a file format.

Useful ideas:

- Convert document content into an LLM-friendly intermediate view.
- Validate edits before applying them.
- Keep a stable semantic appendix for defined terms, cross-references, and likely issues.
- Use atomic transactions instead of blind bulk replacement.

### `dolanmiu/docx`

Best takeaway: strong patch-oriented document construction.

Useful ideas:

- Template + patch model for edits.
- Declarative document structure.
- First-class treatment of comments, bookmarks, hyperlinks, styles, and TOC.
- A clean separation between document model and OOXML serialization.

### `Xceed DocX`

Best takeaway: practical editing ergonomics for existing documents.

Useful ideas:

- Paragraph-centric mutation API.
- Better style inheritance and style ID handling.
- Fluent operations for insert, append, hyperlink, comment, and formatting.
- Good mental model for editing a live document instead of generating from scratch.

## Current State of Lexitool

The repo already has strong low-level support:

- `openxml_opc.py` for relationships and content types.
- `openxml_runmap.py` for text span mapping.
- `comment_ops.py` for comments.
- `edit_ops.py` for insert/replace/delete.
- `markup.py` for read/write markup.
- `header_footer_ops.py` for header/footer operations.

The main gap is not raw capability. The gap is a higher-level legal workflow layer.

## Priority Improvements

### 1. Add a patch transaction layer

Create a document patch abstraction that can bundle multiple edits into one planned action.

Why:

- Legal review usually needs a sequence of controlled edits.
- We should be able to plan, apply, verify, and summarize a patch as a unit.

Expected shape:

- collect edits
- resolve anchors
- validate targets
- apply changes
- re-read document for verification
- emit a change summary

### 2. Add paragraph semantic state

Each paragraph should carry a richer state object, not just text.

Suggested fields:

- paragraph role
- style ID
- numbering state
- anchor metadata
- review risk flags
- header/footer flag
- cross-reference presence

Why:

- Legal drafting depends on structure, not just text.
- This makes review and revision workflows more reliable.

### 3. Unify style inheritance rules

We need one consistent rule set for inherited formatting when inserting or replacing text.

Should cover:

- paragraph style
- run font and size
- indentation and spacing
- numbering inheritance rules
- header/footer exclusion rules
- review visual markers like highlight/color

Goal:

- inserted text should look like a human lawyer pasted it into the document.
- preserve formatting without accidentally copying unwanted review styling.

### 4. Unify relationship-based objects

Treat comments, hyperlinks, bookmarks, refs, headers, and footers as one relationship graph layer.

Why:

- These features all rely on OOXML relationships.
- A single helper layer reduces bugs and makes future editing tools easier.

### 5. Build a legal review workflow

Add a first-class workflow for:

1. full document reading
2. clause discovery
3. change planning
4. paragraph-by-paragraph revision
5. format verification
6. final summary

This is the workflow the user actually wants for legal drafting.

### 6. Support hot reload for active sessions

Active sessions should be able to pick up lexitool changes without waiting for a full restart whenever possible.

Why:

- The current pain point is stale imports in long-lived sessions.
- A legal drafting workflow needs a tool layer that can evolve during the session.

Potential implementation:

- module version check
- reload hook for vendored tools
- tool wrapper that rebinds implementation when source changes

## Proposed Implementation Order

### Phase 1

- Fix active-session reload behavior.
- Stabilize insert/replace/comment/edit compatibility.
- Add regression tests for live-process compatibility.

### Phase 2

- Introduce patch transaction abstraction.
- Add semantic paragraph state.
- Extend readback/inspection to expose structural metadata.

### Phase 3

- Unify relationship graph helpers.
- Expand workflow support for legal review and drafting.
- Improve verification summaries and change reports.

## Success Criteria

We should consider the revision successful when:

- existing sessions can pick up lexitool updates safely
- insertions preserve legal formatting by default
- comments and cross-references remain stable
- reading a document exposes structure, not just text
- the workflow can plan and execute clause-level revision steps
- the user can review and revise documents paragraph by paragraph

## Files To Watch

- `vendor/lexitool/lexitool/edit_ops.py`
- `vendor/lexitool/lexitool/markup.py`
- `vendor/lexitool/lexitool/openxml_opc.py`
- `vendor/lexitool/lexitool/comment_ops.py`
- `vendor/lexitool/lexitool/header_footer_ops.py`
- `tools/lexitool_tool.py`
- `hermes_cli/web_server.py`
- `web/src/pages/ChatPage.tsx`

