# Requirements

Format and rules: [S1](specs/S1-data-model.md), [S2](specs/S2-status-and-lint.md). Markdown stand-in for the R# table; criteria are in [acceptance.md](acceptance.md).

Header line: `status | priority | phase | features | specs | criteria`.

## R1 One store, many projects

`draft | must | mvp | F1 | S1 | A1,A2`

**Statement.** A user can create a project with a slug and name, and every ID, link and query is scoped to that project.


## R2 Create and edit entities

`draft | must | mvp | F2 | S1,S2,S4 | A3,A4`

**Statement.** A user or agent can add and change R#, A#, F#, S# and DOC# entries through validated commands.


## R3 Link entities

`draft | must | mvp | F3 | S1,S2 | A5,A6`

**Statement.** The system shall let entries be linked many-to-many as defined by the model, and reject links to unknown or retired IDs.


## R4 Enforced status rules

`draft | must | mvp | F2 | S2 | A7,A8`

**Statement.** The system shall reject status changes whose exit condition is not met and name the missing field or rule in the error.


## R5 Compact output

`draft | must | mvp | F2 | S4 | A9`

**Statement.** The system shall print one line per item by default and return detail only on request.


## R6 Trace

`draft | must | mvp | F4 | S4 | A10`

**Statement.** Given one entry, the system shall list everything connected to it across all layers and to external cards.


## R7 Next work

`draft | must | mvp | F4 | S2,S4 | A11`

**Statement.** The system shall list the unblocked entries to work on next, ordered by priority and dependency.


## R8 Lint

`draft | must | mvp | F4 | S2 | A12,A13`

**Statement.** The system shall report traceability gaps and inconsistencies with a severity, and exit non-zero on errors.


## R9 Feature graph

`draft | should | mvp | F3 | S1,S2 | A14,A15`

**Statement.** The system shall keep feature dependencies acyclic, support optional edges, derive the critical path and export a diagram.


## R10 Supporting documents

`draft | must | mvp | F5 | S1,S4 | A16,A17`

**Statement.** A user can attach reference documents (DOC#) to any entry; the tool shows a summary by default and reads the body in bounded slices on request.


## R11 PRD references

`draft | should | mvp | F4 | S4 | A18`

**Statement.** The system shall index bracketed ID references in the PRD and supporting documents and report broken ones.


## R12 External links

`draft | should | v1.1 | F7 | S5 | A19,A20`

**Statement.** The system shall link entries to kanban cards or tickets and apply card movement to execution status one-way, never marking anything verified.


## R13 Text export and import

`draft | must | mvp | F6 | S3 | A21,A22`

**Statement.** The system shall export a project to plain text (rows as YAML, bodies as markdown) and import it back without loss.


## R14 Agent guidance

`draft | should | mvp | F8 | S4 | A23`

**Statement.** The plugin shall tell an agent working in a project's directory how to use the tools, without adding anything to sessions elsewhere.


## R15 Code references

`draft | could | growth | F9 | S1 | A24`

**Statement.** Features and specs may list code paths; the system shall report code packages and documents no entry accounts for.


## R16 Documents first

`draft | must | docs | F10 | S6 | A25`

**Statement.** This repository's own documents shall follow the model in markdown so a later import is mechanical.

## R17 Desktop navigation

`draft | should | v1.1 | F11 | S4 | A26,A27,A28,A29,A33`

**Statement.** A user can create projects, switch between them as with kanban boards, land on the rendered PRD, and switch between the PRD, requirements, acceptance, features, specs and documents views, so that all product knowledge is reachable from one page.

## R18 Card views by state

`draft | should | v1.1 | F11 | S4 | A30,A31,A32`

**Statement.** The desktop page shall show requirements, acceptance criteria, specs and documents as cards grouped and filterable by state, and features as cards with a switch to a dependency graph, in the same way the kanban and feature-graph pages do.

