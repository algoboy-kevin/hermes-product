# S2 Status rules and lint

`draft | requirements R2,R3,R4,R7,R8,R9 | features F2,F3,F4`

## 1. Status machines

**Requirement:** `draft` -> `approved` -> `in_progress` -> `done`, and `dropped` from any state.

| Move | Exit condition |
|---|---|
| draft -> approved | Rationale set and at least one feature linked |
| approved -> in_progress | At least one spec and at least one criterion exist |
| in_progress -> done | Every linked A# is `passed` or `waived` |
| any -> dropped | `status_reason` set (and `superseded_by` if replaced) |

**Feature:** `planned` -> `in_progress` -> `shipped`, and `cut`.
`shipped` needs every linked requirement `done`. `in_progress` is refused while a mandatory dependency is `planned`, unless overridden with a recorded reason. `cut` needs a reason and no open requirement left only on this feature.

**Spec:** `draft` -> `approved` -> `implemented` -> `verified`, and `superseded`.
`approved` needs one linked requirement; `implemented` needs an implementation reference in `notes` or an external link; `verified` needs its linked criteria to pass.

**Criterion:** `not_run`, `passed`, `failed`, `waived`. `passed` and `waived` need `evidence`; `waived` needs a reason.

Backward moves (`done` -> `in_progress`) are allowed and logged. Changing the statement of an `approved` or later requirement is logged and marks linked specs stale. Deferring is a change of `phase`, not of status.

## 2. Enforced in code

Reject unknown or retired IDs in links and `superseded_by`; invalid transitions; missing fields for the target status; dependency cycles; marking an R# `done` while any criterion is `not_run` or `failed`. Every error names the missing field or rule so the caller can fix it in one step.

## 3. Lint

| Flag | Applies to | Condition | Severity |
|---|---|---|---|
| orphan_requirement | R# | `approved` or later with no linked feature | Warning |
| unspecified | R# | `in_progress` with no linked spec | Warning |
| no_criteria | R# | `in_progress` or later with no A# | Error (Warning if `legacy`) |
| unverified_done | R# | `done` with a criterion not `passed` or `waived` | Error |
| orphan_spec | S# | `approved` with no linked requirement | Error (Warning if `legacy`) |
| stale_spec | S# | Linked requirement statement changed after approval | Warning |
| spec_behind | S# | `draft` or `approved` while a linked F# is `shipped` or a linked R# is `done` | Warning |
| feature_without_requirement | F# | `planned` or later, no requirement, not `infra` | Warning |
| at_risk | R#, F# | A dependency is blocked or cut | Warning |
| dependency_cycle | F#, R#, S# | Cycle in dependency edges | Error |
| broken_reference | PRD, DOC, R#/F#/S# body | Mention of an ID that does not exist or is retired | Error |
| cut_with_open_requirements | F# | `cut` while its requirements are not `dropped` or reassigned | Error |
| orphan_doc | DOC# | `current` with no link | Warning |
| doc_drift | DOC# | `body_ref` content hash differs from the stored hash | Warning |
| thin_summary | S#, DOC#, F# | The one-line `summary` / `description` restates the title, is an import placeholder (`Imported ... (legacy)`), is only a file path, or is under 4 words | Warning |
| status_in_body | R#, F#, S#, DOC# | A `Status:` line in a body (outside code fences): state lives in the row's status field | Warning |
| uncovered_path | project | Code directory or document no F#/S# `paths` entry covers (opt-in `--coverage`) | Info |
| no_card_links | project | An `approved` or later R# or `planned` or later F#, and no `kanban` external link | Info |
| card_without_ref | kanban card | `ready` or `running` and its body cites no R#/F#/S# (`product:` line, else a bare `R#/F#/S#` token in the first 10 body lines). Exempt: every other column (triage, blocked, ...) and a project with no linked board | Warning |
| completed_without_review | kanban card | `completed` at or after 2026-11-25T09:00:00Z (`REVIEW_RULE_FROM`) with a `claimed` event before it and no `review_requested` event before it. Exempt: a card never claimed (closed by the parent without a run — no run to review), a card completed before the rule date, and a project with no linked board | Warning |

The two card checks read the boards the project is linked to (the `<board>/<card>` ids of its `kanban` external links); a project with no board reports neither. `lint` exits non-zero when any Error is present.

## 4. Derived values

`next` lists a `planned` or `in_progress` feature as ready only when something stands behind it: a requirement, a spec, a code path, `infra`, or another feature that needs it. A feature with none of these is printed apart as `unplanned` (and never counted as ready); it still blocks whatever waits for it.

| Value | Definition |
|---|---|
| Requirement progress | Share of its criteria `passed` or `waived` |
| Feature progress | Share of its requirements `done` |
| Phase readiness | Requirements `in_progress` or later divided by requirements in that phase |
| Critical path | Longest chain of mandatory feature dependencies |
| Strongest evidence | Highest `evidence_level` among a requirement's passed criteria; the weakest decides "how proven" |
