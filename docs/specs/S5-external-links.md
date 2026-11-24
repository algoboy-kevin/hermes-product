# S5 External links (kanban and others)

`draft | requirements R12 | features F7`

| Field | Type | Req. | Description |
|---|---|---|---|
| entity_type | enum | Y | `requirement`, `feature`, `spec`, `criterion` |
| entity_id | int | Y | |
| system | text | Y | `kanban`, `github`, ... |
| external_id | text | Y | Card or ticket key |
| url | text | N | Direct link |
| role | enum | N | `implements`, `tracks`, `verifies` |

## Sync rules

- **Status direction is one-way per field.** Card column drives the *execution* status of specs and features through a configurable column-to-status mapping. The store owns links, criteria, priority and requirement status.
- Default mapping: Backlog = `approved`, In progress = `in_progress`, In review or Done = `implemented`. **Nothing maps to `verified`**: that comes only from criteria with evidence. A finished card is evidence, not proof.
- A requirement may move to `done` only through its criteria ([S2](S2-status-and-lint.md)).
- A card moved backward after its entry is `verified` raises a warning instead of changing data.
- A card links to entries by ID in its body (`product: F7, S12`); `sync` proposes the link rows from those lines and applies them only on request.
- `lint` reminds with one project-level Info (`no_card_links`) when an entry is `approved` or later and no kanban card is linked.
