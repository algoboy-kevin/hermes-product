# DOC2 Desktop page layout

`kind: design | status: current | links: F9, R14 | note: phase v1.1`

**Summary.** The single desktop page: a project switcher, a status filter, five focus views and, for features only, a board or graph toggle.

## Layout

```
[ project name v ]  [ status tabs ]  [ view mode: board | graph ]
[ PRD | Requirements | Acceptance | Features | Specs | Docs ]
<content>
```

- **Project switcher** (top left): lists projects; the project whose root contains the working directory is preselected.
- **Status tabs:** filter the content by status. Hidden on the PRD view, which has no status.
- **View mode** (board or graph): shown only for Features. Board groups by status; graph draws the dependency edges and the critical path.
- **Focus buttons:** PRD, Requirements, Acceptance, Features, Specs, Docs. One view at a time over the same project.
- **Content:** PRD renders the markdown; the other views list entries one line each, opening a detail panel on click with links, criteria, history and linked documents.
- Read-only by design: the page names the command that edits each field instead of offering an edit surface.
