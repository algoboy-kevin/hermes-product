---
name: using-product
description: "Use when a repo has a product store (R#/F#/S#) or plan work."
---

# Using the product store

Product knowledge (R#/A#/F#/S#/DOC# entries) lives in the product store, not in
docs. Status lives in rows; documents hold bodies and rationale only.

## Before you read or grep

Before reading or grepping documents, run these from inside the project:

```bash
hermes product trace <REF>    # everything connected to an entry
hermes product next           # what to work on next
hermes product lint           # what is inconsistent
```

`trace` first for any entry you were pointed at; `next` before picking work;
`lint` before trusting consistency.

## Choosing the project

`--project SLUG` names the project explicitly. Without it, the project whose root
contains the working directory is used. `hermes product project list` shows all.

## Reading the compact output

One line per item:

```
R3  approved  must  mvp  title  F:..  S:..  A:2/4
```

ref, status, priority/layer, phase, title, then links (`F:`/`S:`/`R:`) and
criteria progress (`A:passed/total`). `show <REF>` prints full detail (statement,
criteria, notes, history). A body is read separately, always bounded:
`doc read DOCn --section H --max-chars N` (reports truncation).

## Rules

- Status lives in rows, never in documents.
- An entry is `verified`/`done` only from its criteria: passed or waived with
  evidence. A finished card is not proof.
- Never hand-edit exported YAML to change status.
- Changes go through `set`/`link` (validated: exit 1 with a message naming the
  missing field).

## Verbs that exist

project (add/list/use), add, set, link/unlink, show, list, trace, next, lint,
graph, doc read, scan-prd, export, import.

## Full specification

`docs/specs/S4-tool-behavior.md` of the product repo.
