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
- `sync` ends with a read-only `reconcile` block: a feature whose linked cards all map to `implemented` while its status is below `shipped` prints `reconcile F13  in_progress, 3/3 cards done: ready to ship? (owner decides)`.
- A requirement whose tracking cards all map to `implemented` and that still has a criterion neither `passed` nor `waived` prints `reconcile R24  tracked by 3 cards, all done; criteria 0/9 passed: run the criteria`.
- The block writes nothing and adds no status: it names the owner's next step. An entry with an open card, and a card whose column maps to nothing, print nothing.
