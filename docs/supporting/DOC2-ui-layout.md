# DOC2 Desktop page layout

`kind: design | status: current | links: F11, R17, R18 | note: phase v1.1`

**Summary.** The single desktop page: a project switcher with a New project dialog, six focus views that open on the rendered PRD, card views grouped by state, and for features only a Cards | Graph toggle.

## References (read, do not copy blindly)

- Switcher, header band, `ctx.storage`: the kanban-gantt plugin (`~/.hermes/plugins/kanban-gantt`, `src/ui/TitlebarBoardSwitcher.tsx`, `src/main.ts`) and the official kanban plugin.
- Cards, state chips with counts, Cards|Graph toggle, New dialog, detail panel: the feature-graph plugin (`hermes-feature-graph`, `desktop/plugin.js`, `dashboard/plugin_api.py`).
- The default content is cards like the Hermes kanban card, never the gantt timeline.

## Layout

```
[ project v  + New project ]                                  (page header band)
[ PRD | Requirements | Acceptance | Features | Specs | Docs ]  (focus buttons)
[ All . n | draft . n | approved . n | ... ]   [ Cards | Graph ]  (status tabs; toggle on Features only)
<content>                                                      | detail panel
```

## Behaviour

1. **Projects.** The switcher lists projects and remembers the last one (`ctx.storage`). **New project** opens a dialog (slug, name, optional root); the create call writes only to the Hermes store. A bad or duplicate slug is refused with a message ([A26], [A27]).
2. **Landing.** Opening the page, or switching project, shows the PRD view: the PRD document as rendered markdown, no status tabs. No PRD yet: an empty state naming the command that adds one ([A28]).
3. **Focus views.** Six buttons swap the content in place; the active one is highlighted ([A29]).
4. **Cards by state.** Requirements, Acceptance, Specs, Docs and Features default to cards: id, title, status chip, one-line summary, link counts. Status tabs show `All` plus one tab per state with counts. `All` sorts by workflow state order then id; a state tab shows only that state ([A30], [A31]).
5. **Workflow order.** Requirements: draft, approved, in_progress, done, dropped. Features: planned, in_progress, shipped, cut. Specs: draft, approved, implemented, verified, superseded. Acceptance: failed, not_run, passed, waived. Docs: current, superseded.
6. **Features.** A Cards | Graph toggle as on the feature-graph page: cards by default; Graph draws dependency edges (optional ones dashed) and highlights the critical path; selecting a node opens the same detail panel ([A32]).
7. **Detail panel.** Fields, links, criteria, linked documents (summary only, body on request), history, and the command that edits the entry. An R#, F# or S# with a body (S1 section 3.1) shows a `Show body (N KB)` button: the text is fetched from `GET /project/{slug}/entry/{ref}/body` only when clicked, bounded to 20000 characters, with the CLI command that reads the rest; switching entries closes it. Read-only: the only write control on the page is New project ([A33]).
