# S1 Data model

`draft | requirements R1,R2,R3,R9,R10,R15 | features F1,F2,F3,F5,F9`

Version 0.4 (schema v2: entity bodies, section 3.1). Supersedes the 0.2 draft of the traceability data specification; section "Changes" lists why.

## 1. Conventions

- **IDs.** Integer primary key per entity type *within a project*, displayed with a prefix: `R3`, `A21`, `F7`, `S2`, `DOC1`. Never reused or renumbered; retire or supersede instead of deleting.
- **References in markdown.** `[R3]`, `[A21]`, `[F7]`, `[S2]`, `[DOC1]`; `[F:stripe]` for a feature slug. A linter checks every reference resolves to an existing, non-retired entry.
- **Types.** text, longtext (markdown), enum, int, bool, ref. **Req.** column: Y required, N optional, D derived (never entered by hand).
- **Common fields.** `id` (Y), `title` (Y, 3-80 chars, except A#), `status` (Y), `notes` (N, short), `updated_at` (D).

## 2. Project

| Field | Type | Req. | Description |
|---|---|---|---|
| slug | text | Y | Unique short key, lowercase, hyphenated |
| name | text | Y | Display name |
| root | text | N | Directory of the code repository, used to pick the project from a working directory |
| prd | ref DOC | D | The one DOC of kind `prd` |

## 3. Requirement (R#)

What the product must do, and why. Never the technology.

| Field | Type | Req. | Description |
|---|---|---|---|
| statement | longtext | Y | One testable sentence: "A <actor> can <action> so that <outcome>" or "The system shall <behavior> under <condition>" |
| rationale | text | N | One or two sentences. Required at `approved` |
| priority | enum | Y | `must`, `should`, `could` |
| phase | text | N | Target release (`mvp`, `v1.1`, `growth`) |
| status | enum | Y | `draft`, `approved`, `in_progress`, `done`, `dropped` |
| status_reason | text | N | Required for `dropped` |
| superseded_by | ref R | N | Set when replaced (status stays `dropped`) |
| blocked / blocked_reason | bool / text | Y / N | Flag, default 0; reason required when 1 |
| hypothesis_ref | text | N | Optional link to the claim this requirement tests (any tracker or document) |
| legacy | bool | Y | Default 0. Imported from an existing system without full detail; lint downgrades some errors to warnings |
| prd_ref | text | N | PRD anchor, such as `PRD.md#goals` |
| body | longtext | N | The full text that defines the requirement (section 3.1). Never printed by `list`, `trace` or `next` |

Links: features (F#, many-to-many), specs (S#), criteria (A#, one-to-many, child records), depends_on (R#), docs (DOC#), external.

### 3.1 The entity body

R#, F# and S# carry an optional `body`: the full text that **defines that one entry**, stored on the row (schema v2, column `body`). It is what a kanban card's body is to a card.

- **Role decides, size does not.** Text that defines one entry is its body. Text about the project, a period or evidence about things (the PRD, a UX review, a run report) is a DOC (section 7). An 84 KB contract that defines one spec is still that spec's body.
- **Retrieval cost.** `list`, `trace` and `next` never print a body. `show` prints one line (`body: N chars (show S3 --body)`); `show REF --body [--section H] [--max-chars N]` reads it, always bounded, reporting truncation.
- **The one-line field stays explicit.** `summary` (S#), `description` (F#) and `statement` (R#) are written by a human or agent and are never derived from the body.
- **Where the prose goes.** S# body = the contract the code is built against; R# = one testable sentence (the body is optional context); F# = the capability and its boundary.
- **A# has no body.** A criterion is one sentence plus evidence; context belongs on its requirement.
- **Refusals, not drops.** `--body` / `--body-file` on A#, `--body-ref` on anything but DOC#, and two body options at once are errors; nothing is created.
- **History.** Changing a body logs `body changed` (with `--reason` when given). Setting the same text again logs nothing; a blank body clears it (NULL).
- **A body never carries status** (same rule as a DOC): what is done or blocked lives in the row's fields.

## 4. Acceptance criterion (A#)

One pass/fail check owned by exactly one requirement.

| Field | Type | Req. | Description |
|---|---|---|---|
| requirement_id | ref R | Y | Parent |
| text | longtext | Y | One check, Given/When/Then recommended; its identity (no title) |
| status | enum | Y | `not_run`, `passed`, `failed`, `waived` |
| evidence | text | N | Required when `passed` or `waived`: test name, link or short reason |
| evidence_level | enum | N | How strong the proof is: `none`, `unit`, `integration`, `staging`, `production`. Default `none`; set when `passed` |

## 5. Feature (F#)

A capability that delivers requirements; a node in the dependency graph.

| Field | Type | Req. | Description |
|---|---|---|---|
| slug | text | N | Unique short key |
| layer | enum | Y | `foundation`, `core`, `growth` |
| status | enum | Y | `planned`, `in_progress`, `shipped`, `cut` |
| status_reason | text | N | Required for `cut` |
| phase | text | N | Target release |
| description | text | N | One or two sentences on what it is and where its boundary is |
| infra | bool | Y | Default 0. A feature with no requirement of its own (plumbing); exempt from `feature_without_requirement` |
| paths | list text | N | Code paths (directories or files) that implement it. Enables the coverage check ([R15]) |
| blocked / blocked_reason | bool / text | Y / N | As for R# |
| body | longtext | N | The full text that defines the capability and its boundary (section 3.1) |

Links: requirements (R#), specs (S#), depends_on (F# to F#, each edge with an `optional` flag), docs (DOC#), external.

## 6. Spec (S#)

How something is built or must behave, precisely enough to implement and verify.

| Field | Type | Req. | Description |
|---|---|---|---|
| summary | text | Y | One or two sentences of what this spec decides |
| status | enum | Y | `draft`, `approved`, `implemented`, `verified`, `superseded` |
| status_reason / superseded_by | text / ref S | N | `superseded_by` required when `superseded` |
| paths | list text | N | Code paths it describes |
| blocked / blocked_reason | bool / text | Y / N | As for R# |
| body | longtext | N | The full contract (section 3.1). A spec with no body is visibly thin |

The full text of a spec is its `body`. A DOC linked to a spec holds context about it (a design review, a run report), not the spec itself. Schema v1 stored spec text as a DOC of kind `spec`; those rows keep working and are folded into bodies by a separate migration.

Links: requirements (R#, at least one from `approved`), features (F#), depends_on (S#), docs (DOC#), external.

## 7. Supporting document (DOC#)

Reference material too large for the PRD or a kanban card: a design write-up, an API description, a research note, a runbook. **A document never holds status**: what is done or blocked lives in the rows.

| Field | Type | Req. | Description |
|---|---|---|---|
| kind | enum | Y | `prd`, `spec`, `context`, `research`, `design`, `api`, `runbook`, `other`. Exactly one `prd` per project |
| summary | text | Y | One or two sentences. This is what an agent sees by default |
| body | longtext | N | Markdown stored with the project. Alternative: `body_ref` |
| body_ref | text | N | External path or URL; the store keeps a content hash to detect drift |
| status | enum | Y | `current`, `superseded` |
| superseded_by | ref DOC | N | Required when `superseded` |

Links: any entry (PRD, R#, F#, S#, A#), many-to-many, via the `doc_link` table.

## 8. Link tables

| Table | Between | Notes |
|---|---|---|
| feature_requirement | F# and R# | Which features deliver which requirements |
| spec_requirement | S# and R# | Each spec needs at least one from `approved` |
| feature_spec | F# and S# | Optional direct link |
| feature_dependency | F# to F# | Graph edges with `optional` flag |
| requirement_dependency | R# to R# | Prerequisite requirements |
| spec_dependency | S# to S# | Implementation order |
| doc_link | DOC# and any entry | Supporting material |
| external_link | any entry and an external system | See [S5](S5-external-links.md) |

## 9. History

One compact log: `entity_type`, `entity_id`, `change` (such as `status approved -> in_progress`), `reason` (required when a requirement statement or spec body changes), `at`. Written automatically for status changes, statement changes and link additions or removals.

## 10. Changes from 0.2

| Change | Reason |
|---|---|
| Project table; IDs scoped per project | One store must hold many projects |
| DOC# entity and `doc_link` | Long reference material needs a home that is not the PRD or a card |
| `evidence_level` on A# | "Done" differs in strength: a unit test is not a production check |
| `paths` on F# and S#; `infra` on F# | Coverage check; plumbing features have no requirement of their own |
| `hypothesis_ref`, `legacy` on R# | Optional link to the claim a requirement tests; honest import of existing systems |
| `body` on R#, F#, S# (schema v2); `show --body` | A spec's text lived in a second entity, so `add S` produced a pointer with no text; the card model (text on the entry) removes the hop. DOC narrows to context |
| Kanban `Done` maps to `implemented`, never `verified` | `verified` must come from criteria ([S5](S5-external-links.md)) |

Removed in 0.2 and still removed: owner, approvals, value/risk/effort, dates, tags, open_questions, interfaces/assumptions/constraints/alternatives/risks on specs, per-field versioning. Add back only when a real query needs them.
