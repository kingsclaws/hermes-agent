# WebUI → Desktop UI Migration Plan (Direction A)

Status: Phase 1-3 complete. Paused for Direction B (remote gateway).

## Completed

- [x] Parametric 5-surface color system (seeds.ts, index.css)
- [x] Desktop multi-layer shadows (5 tiers)
- [x] UI tokens (stroke 4-level, fill 5-level, text 4-level)
- [x] Light/Dark/System mode toggle
- [x] PaneShell CSS Grid layout + drag resize
- [x] SideRail icon column
- [x] DesktopSidebar with real session list
- [x] RightPanel with Files/Review/Preview tabs
- [x] StatusBar
- [x] ChatView + ChatBar components (mirror Desktop ChatView)
- [x] Nous theme as default (#0c0c0e bg, #0053FD accent)
- [x] Old sidebar removed
- [x] 10 color math helpers (color.ts)
- [x] User theme store (user-themes.ts)
- [x] Boot-time flash prevention (index.html script)

## Remaining

- [ ] Command palette → Desktop command-center integration
- [ ] Session sidebar with virtual scrolling + project groups
- [ ] Right rail preview pane (file preview, console)
- [ ] Composer: rich text editor, slash commands, @ completions
- [ ] Composer: model pill, queue panel, status stack
- [ ] Thread: assistant-ui message rendering
- [ ] ChatHeader: session actions menu
- [ ] File tree browser in right panel
- [ ] Frontend unit tests (vitest setup)

## Key Files

| File | Purpose |
|------|---------|
| `web/src/components/AppShell.tsx` | Main shell (SideRail+Sidebar+Main+RightPanel+StatusBar) |
| `web/src/components/PaneShell.tsx` | CSS Grid layout system |
| `web/src/components/SideRail.tsx` | Icon column |
| `web/src/components/ChatView.tsx` | Desktop ChatView mirror |
| `web/src/components/ChatBar.tsx` | Desktop composer mirror |
| `web/src/themes/seeds.ts` | Palette → CSS seed derivation |
| `web/src/themes/color.ts` | 10 color math functions |
| `web/src/themes/user-themes.ts` | localStorage theme store |
| `web/src/index.css` | Parametric surface system + shadows + tokens |
| `web/src/store/layout.ts` | Zustand layout store |
| `web/src/store/panes.ts` | Zustand panes store |

## Resuming

1. `cd web && npm run dev` to start dev server
2. Pick an item from Remaining list
3. Each component should reference Desktop source at `apps/desktop/src/`
