# PRD: hermes-product

Version 0.1 (draft), 2026-11-23. Status: draft.

## 1. Summary {#summary}

`hermes product` is a Hermes plugin that keeps a project's product knowledge (PRD, requirements,
features, specs, acceptance criteria, supporting documents) in a small SQLite store that LLM agents
read and write through compact tools. Humans read it in a desktop page and in a plain-text export.
The code repository keeps only code, a README and configuration.

## 2. Problem {#problem}

- Product knowledge is scattered across markdown files in the code repo: plans, progress logs,
  delivery indexes, per-task briefs. They duplicate each other and drift; a feature can be marked
  "off" in one file and "on" in the config.
- An agent starting work has to grep many files to learn what exists, what is planned, what blocks
  what, and what "done" means. That costs tokens and gives inconsistent answers.
- Kanban cards track execution but not the *why*: no link from a card up to a requirement, or from a
  feature down to proof that it works.
- Status typed by hand disagrees with reality. Nothing derives "done" from evidence.

## 3. Users {#users}

| Role | Needs |
|---|---|
| Product owner (human) | Write and approve the PRD and requirements; see what is done and what is blocked; review in one place. |
| Coding agent (LLM) | Orient in two or three tool calls; know exactly what to build and how it is verified; record progress without inventing structure. |
| Reviewer (human or LLM) | Trace a change back to a requirement and forward to its evidence. |

## 4. Goals {#goals}

1. One source of truth for product structure, status and links; markdown narrative stays in the PRD
   and supporting documents ([R1], [R2], [R10]).
2. Status is derived from evidence wherever possible; invalid states are rejected by the tool ([R4], [R8]).
3. Agents get compact, token-cheap answers by default ([R5], [R6], [R7]).
4. The whole project exports to plain text that diffs cleanly in git ([R13]).
5. Code repositories need no product documents ([R14], [R15]).

## 5. Non-goals {#non-goals}

- Not a task tracker: execution stays on a kanban board, linked from here ([R12]).
- Not a project-management suite: no estimates, assignees, approvals, sprints or dates in the model.
- Not a wiki: long reference material is allowed as supporting documents, but status never lives in them.
- Not multi-user or hosted in v1: one local store per machine.

## 6. Scope by phase {#scope}

| Phase | Contents |
|---|---|
| `docs` (now) | This repository as markdown, following the model by hand ([R16]). |
| `mvp` | Store, entities, links, validation, trace, next, lint, supporting documents, text export/import, agent guidance ([R1] to [R11], [R13], [R14]). |
| `v1.1` | External links to kanban with one-way status mapping ([R12]); desktop page: projects, PRD viewer, card views by state, feature graph ([R17], [R18]). |
| `growth` | Code references and coverage check ([R15]). |

## 7. Principles {#principles}

1. Links are the value; every other field is optional metadata.
2. Few fields, few states: an extra field is something an LLM will leave empty or fill with invented content.
3. Rules live in code, not in the prompt.
4. Compact by default, detail on request.
5. The PRD and supporting documents hold narrative; the store holds structure, status and links.
6. A finished task is evidence, not proof: "verified" comes only from acceptance criteria.

## 8. Success measures {#success}

- An agent in a fresh session answers "what depends on X and is it done?" correctly in at most three tool calls.
- Zero status-tracker or delivery-index files in a code repository using the plugin.
- `lint` reports no errors on a project before a release.
- Export then import reproduces the store byte-for-byte in its text form.

## 9. Risks {#risks}

| Risk | Mitigation |
|---|---|
| Agents ignore the tools unless pointed at them | Registered skill, conditional prompt section, and a fixed contract line on every task ([R14], [DOC1]). |
| Backfilling an existing project means inventing requirements | Import features and specs first; flag imported items `legacy` so lint warns instead of erroring ([S6]). |
| Store lives only on one machine | Scheduled text export committed to a git repository ([R13]). |
| Over-modelling | Fields removed in v0.2 stay removed until a real query needs them ([S1]). |

## 10. Open decisions {#open}

- Criterion numbering: global `A#` (chosen for now) or per requirement (`R3.1`).
- Whether `feature_spec` is stored or always inferred through shared requirements (stored for now).
- Kanban column-to-status mapping defaults ([S5]).
- Where the git copy of the text export lives for a given project.

Decisions carried over from hermes-feature-graph (superseded 2026-11-23; its tag v0.3.0 is kept, its store and parked cards are archived, the `gmd` graph was migrated by `migrate_fg.py`):

- Rot prevention: no CI gate or hook in the plugin. `lint` (and `lint --coverage`) runs at the card-close step; a repository may call `hermes product lint` from pre-commit. An automated sync engine stays future work, only if manual links plus `lint` prove insufficient.
- Multi-repo: one project per repository root. A cross-repository capability is a note on an entry naming the sibling project. Revisit when a second real project needs a reference.
- Spec lock: not built. Agents read the store and seldom write it (FG-R1 test; the HP-11 pilot found the same for reading without a hint); lock guards, change logs and agent write tools are reopened only on a real incident of definition drift.
- Where things live: an entry holds title, statement and notes (blockers, a design-note path); repository documents hold contracts, runbooks, evidence and unbuilt design notes, linked as DOC entries; kanban cards hold briefs, reports and reviews. Status never lives in documents.
- Agent guidance: a registered skill, a conditional prompt section (only inside a project root) and the `Product: <slug>` line in the worker contract. Unhinted discovery was 2 of 4 runs, hinted 4 of 4 (HP-11 pilot).
