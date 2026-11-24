---
name: using-product
description: "Use when a repo has a product store (R#/F#/S#) or plan work."
---

# Using the product store

Product knowledge (R#/A#/F#/S#/DOC# entries) lives in the product store, not in
docs. Status lives in rows; documents hold bodies and rationale only.

## Before you read or grep

Run these first, from inside the project, before reading or grepping documents:

```bash
hermes product trace <REF>    # everything connected to an entry
hermes product next           # what to work on next
hermes product lint           # what is inconsistent
```

`trace` first for the entry you were pointed at; `next` before picking work; `lint` before trusting consistency.

## Choosing the project

`--project SLUG` names the project explicitly; without it, the project whose root contains the working directory is used. `hermes product project list` shows all.

## Reading the compact output

One line per item:

```
R3  approved  must  mvp  title  F:..  S:..  A:2/4
```

ref, status, priority/layer, phase, title, then links (`F:`/`S:`/`R:`) and
criteria progress (`A:passed/total`). `show <REF>` prints full detail (statement,
criteria, notes, history). Text is read separately, always bounded: an R#/F#/S#
body with `show <REF> --body --section H --max-chars N`, or a DOC with `doc read DOCn ...` (both report truncation).

## Where text goes

- Text defining ONE entry (a spec's contract, a feature's boundary) is that entry's body: `add S ... --body-file F`, `set S3 --body-file F`.
- Text about the project or evidence (PRD, UX review, run report) is a DOC.
- A# has no body; a refused `--body` is an error, never a silent drop.

## Rules

- Status lives in rows, never in documents.
- An entry is `verified`/`done` only from its criteria: passed or waived with evidence. A finished card is not proof.
- Never hand-edit exported YAML to change status.
- Changes go through `set`/`link` (validated: exit 1, naming the missing field).

## Cards

A card that does work for an entry carries `product: F# R#` in its body. When you
close such a card, run `hermes product sync --board SLUG` (dry run), read it, then
add `--apply`. Sync moves execution statuses only, never `verified` or `shipped`.

## Faster: apply a plan

`hermes product apply plan.yaml` runs `ops` of add/set/link/unlink in ONE process, `$alias`
naming an earlier `add ... as:`; `--dry-run` rehearses, one bad op refuses the whole batch.

## Verbs that exist

project (add/list/use), add, set, link/unlink, show, list, trace, next, lint,
graph, doc read, scan-prd, export, import, apply, sync.

## Full specification
`docs/specs/S4-tool-behavior.md` of the product repo.
