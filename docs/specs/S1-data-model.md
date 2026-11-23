# S1 Data model

`draft | requirements R1,R2,R3,R9,R10,R15 | features F1,F2,F3,F5,F9`

Version 0.3. Supersedes the 0.2 draft of the traceability data specification; section "Changes" lists why.

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

Links: features (F#, many-to-many), specs (S#), criteria (A#, one-to-many, child records), depends_on (R#), docs (DOC#), external.

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

The full text of a spec is a DOC (kind `spec`) linked to it; the S# row carries only the summary and status.

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
| Kanban `Done` maps to `implemented`, never `verified` | `verified` must come from criteria ([S5](S5-external-links.md)) |

Removed in 0.2 and still removed: owner, approvals, value/risk/effort, dates, tags, open_questions, interfaces/assumptions/constraints/alternatives/risks on specs, per-field versioning. Add back only when a real query needs them.
