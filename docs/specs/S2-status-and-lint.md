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
| feature_without_requirement | F# | `planned` or later, no requirement, not `infra` | Warning |
| at_risk | R#, F# | A dependency is blocked or cut | Warning |
| dependency_cycle | F#, R#, S# | Cycle in dependency edges | Error |
| broken_reference | PRD, DOC | Mention of an ID that does not exist or is retired | Error |
| cut_with_open_requirements | F# | `cut` while its requirements are not `dropped` or reassigned | Error |
| orphan_doc | DOC# | `current` with no link | Warning |
| doc_drift | DOC# | `body_ref` content hash differs from the stored hash | Warning |
| uncovered_path | project | Code directory or document no F#/S# `paths` entry covers (opt-in `--coverage`) | Info |

`lint` exits non-zero when any Error is present.

## 4. Derived values

| Value | Definition |
|---|---|
| Requirement progress | Share of its criteria `passed` or `waived` |
| Feature progress | Share of its requirements `done` |
| Phase readiness | Requirements `in_progress` or later divided by requirements in that phase |
| Critical path | Longest chain of mandatory feature dependencies |
| Strongest evidence | Highest `evidence_level` among a requirement's passed criteria; the weakest decides "how proven" |
