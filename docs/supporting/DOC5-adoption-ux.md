# v-next input: adoption UX and the entity body model

Status: draft input for the next version. Nothing here is decided; section 6 lists what needs the
owner's ruling.

Evidence base: the go-mm-driver (`gmd`) migration run on 2026-11-23, done end to end against this
plugin — 45 markdown files, 47 imported features, 29 specs, 46 requirements, 49 criteria, 48
documents. That run is the first real adoption of `hermes product` by a repository that was never
written in this plugin's dialect, and it exposed where the authoring surface does not fit the job.

## 1. What the migration cost

| Step | Volume | How it was done |
|---|---|---|
| Project name fix | 1 field | raw `sqlite3 UPDATE` on `product.db` — no `project set` verb |
| Spec documents | 45 files, 48 DOC rows | 48 × (`add DOC --body-file` + `link`) via a throwaway Python script |
| Spec metadata | 29 S# rows | `set S<n> title=… summary=…` per row |
| Requirements | 46 R# rows | `add R` + 3–4 `link` calls each |
| Criteria | 49 A# rows | `add A` + `set` for the six with evidence |
| Verification | 6 passed criteria | a second throwaway script re-reading `git show HEAD:<path>` to prove each quote exists |

Roughly 300 `hermes product` process starts. Wall clock ~12 minutes, of which almost all is CLI
start-up, not work. Three scripts had to be written outside the tool: a draft validator, a document
importer, a requirement importer. One raw database write was needed to fix a name the CLI cannot set.

## 2. Findings

### 2.1 There is no bulk path for a normal repository

`import` reads this plugin's own export format. `import-md` reads this plugin's own markdown dialect
(numbered headers, backticked header lines, an `index.md` registry). A repository like `go-mm-driver`
is 45 free-form briefs: neither path can take it. The only open door is `add` in a loop, which is the
slowest and least safe option.

Proposal: **`hermes product adopt DIR`**. Point it at a repository; it scans for candidate documents
(specs, architecture, plan, runbooks), proposes S#/F#/R# rows, attaches bodies, prints the plan as a
diff, and writes only with `--apply`. This is the moment a user decides whether to keep using the
plugin, so it deserves to be the best path rather than the missing one.

### 2.2 Required fields accept placeholders

The imported specs carried `summary: Imported spec document docs/specs/… (legacy)` and titles equal
to the file name. Every row passed validation, and `lint` reported nothing about it. A hollow row that
passes validation is worse than a missing one: "migrated" and "not migrated" look identical, which is
the same failure the feature-graph left behind (nodes with no paths, listed as ready).

Proposal: lint check `thin_summary` — a summary that only restates the path, the title, or an
"imported/legacy" template is flagged; or make the field nullable with an explicit `unmigrated`
marker that lint can count.

### 2.3 The S#/DOC split is invisible in the interface

The model (S1 §6) is right: an S# row carries the summary and status, the full text is a DOC of kind
`spec` linked to it. But nothing in `add S --help` says a second entity exists, and `add S` has no
body flag. The natural act — "here is my spec, put it in" — produces a pointer with no text, which is
exactly the defect the owner reported on first inspection.

Proposal: either fold the body onto the entity (§4), or make it one command
(`add S --body-file F` creates the DOC and the link).

### 2.4 No natural key, no upsert, no dry run

`add` always inserts. Re-running an import duplicates everything. A migration is never one pass: it is
run, inspected, corrected, re-run. Without `--if-missing` (keyed by document path, feature slug, or a
requirement key) the rerun is a cleanup job.

Proposal: `--if-missing` on `add`, plus `--dry-run` on every writing verb (`import-md` already has one;
`add` and `import` do not).

### 2.5 No run summary, no machine-readable output

Each failure is one line on stdout ("FAIL add DOC …"), progress is invisible until a call returns, and
a 300-call script has no aggregate result. The scripts here had to collect failures themselves.

Proposal: `--json` on every writing verb and one final summary line (`created 46, skipped 3, failed 0`),
non-zero exit on any failure.

### 2.6 Evidence is stored, never checked

`evidence` is the string grammar `"<verbatim quote>" -- <path>:<line>`. The store accepts any string
and marks the criterion passed. This run had to build a second checker outside the tool to prove the
six quotes really occur in the cited files. If "status derives from evidence" is the product promise,
verification belongs inside the tool.

Proposal: a `verify` verb that walks every passed criterion, resolves the path at a named revision
(default `HEAD`), normalises whitespace and fails on a missing quote; optionally refuse `passed` at
write time when the quote cannot be found.

### 2.7 `next` is not trustworthy

`next (gmd): 11 ready` — eight of those were path-less rollup nodes with no requirement and no spec.
Status is a hand-set enum, so nothing stops a node from advertising itself as ready. A command whose
job is "what now" that lists phantom work teaches the user to ignore it.

Proposal: exclude features with no requirement and no paths from `next`, or list them separately as
`unplanned`; derive readiness from requirement/criteria state where possible.

### 2.8 Vocabulary drift

The same concept — the one sentence you see by default — is called `statement` on R#, `summary` on S#
and DOC, and `description` on F#. Four names, one role. This is what makes the model feel heavier than
it is, and it is the direct cause of the "why does the spec hold no information" reaction: the field
that should have carried the answer is called `summary` but was filled with a path.

Proposal: one name (`description`) on every kind, with `statement` kept only as R#-specific (see §4.4).

### 2.9 Help shape

`add --help` is a 15-flag dump spanning all five kinds; per-kind help (`add DOC --help`) is not a thing.
"Which flags make a spec with its text" is unanswerable from the CLI.

Proposal: per-kind help, and one worked example per kind in the top-level help.

### 2.10 The desktop smoke test does not use the API's real payload

Opening any requirement card crashed the page: `(values || []).map is not a function`. A requirement's
`links` object carries two counters beside the id lists (`criteria_total`, `criteria_passed`,
`store.links_of`), and the renderer assumed every value was an array. The smoke test passed because its
fixture was hand-shaped (`{features: [1], specs: [1], criteria: [1, 2]}`) instead of taken from the
API — so the only path that broke was the one the fixture did not describe.

Proposal: generate UI fixtures from the API (`store.get_entity` for each kind), and keep one render
check that runs against a real project in the store rather than a hand-written object. The same rule
as evidence: a fixture the author shapes proves what the author believed, not what the API returns.

Fixed on 2026-11-23 (frontend guard + counters rendered as `criteria 2/2 passed`, fixture corrected to
the real shape, two new smoke checks).

## 3. Order of work

1. `add --if-missing` + `--dry-run` + `--json` — makes every later step cheap and idempotent.
2. `add S --body-file` (or §4) — removes the two-entity step.
3. `thin_summary` lint — makes a hollow migration visible.
4. `verify` — makes evidence real.
5. `next` cleanup — removes phantom work.
6. `project set` — closes the raw-sqlite hole.
7. `adopt DIR` — the big one, after the above exist.
8. Desktop fixtures from the API, plus one render check against a real project (§2.10).

## 4. The entity body model (owner proposal, 2026-11-23)

Owner's proposal: kanban cards carry their context on the card (title + body), while this plugin makes
a document per entity. Collapse to **title + description**, with a flag to print the full content so
queries are not flooded; documents only for the special case.

Assessment: **agree in direction.** The S#/DOC split is the single biggest source of friction in this
migration, and it is what produced specs that point at files instead of holding them.

### 4.1 Proposed shape

| Field | Rule |
|---|---|
| `title` | as today, 3–80 chars |
| `description` | the default view: 1–2 sentences, bounded (~400 chars), explicit, written by a human or agent |
| `body` | the full text, on the row, returned only by `show --body` (bounded, reports truncation) |

`list`, `trace` and `next` never print `body`. `show` prints title, description and links; `show --body`
adds the text. Retrieval stays as cheap as today, and the two-entity hop disappears.

### 4.2 What stays a DOC

A column cannot be shared. Keep DOC for text that is:

- cited by two or more entities (a runbook serving eight features, an API reference serving a venue and
  its adapter), because copying it onto each row recreates the duplication the plugin exists to kill;
- project-level rather than entity-level: the PRD, research notes, an architecture document, a runbook;
- versioned on a different cadence than the row (a contract that outlives several requirements).

Rule of thumb to put in the help text: **if only one entity owns the text, it is a body; if more than
one cites it, or the project owns it, it is a document.**

### 4.3 What this fixes

- The gmd migration would have been one entity per spec, one command each, no link step: ~100 calls
  instead of ~300, and no "DOC20 spec …" rows in `show` output.
- The failure mode "spec exists, text missing" becomes impossible: a spec with no body is visibly
  thin, not silently valid.
- It matches the mental model users already have from kanban cards.

### 4.4 Where I would not follow it

1. **A# keeps no body.** A criterion is one given/when/then sentence plus evidence. A body invites
   ambiguity into the pass/fail decision.
2. **R# keeps `statement` as its primary field.** A requirement's value is one testable sentence;
   "description" is too soft a container and prose will creep in. Body stays optional context, and the
   normal case is a link to the spec that justifies it.
3. **`description` stays explicit.** Do not derive it from the first paragraph of the body: imported
   documents open with status lines, commit hashes and provenance, which is precisely the noise that
   made the gmd summaries useless.
4. **Bodies must not carry status.** Several imported documents open with `Status: SPEC (contract
   only)` or `Status: DRAFT`. If the body lives on the row, that is a contradiction one line away from
   the real status column. Lint should flag `status:` inside a body.

### 4.5 Cost

For `gmd` the whole spec corpus is ~4,000 lines / ~200 KB — trivial for SQLite, and cheaper than the 48
DOC rows plus 48 links it replaces. The real cost is migration of what already exists: the 48 imported
DOCs would fold into their S# rows, keeping DOC only for the PRD, the architecture document and the
runbooks that serve several features.

## 5. Acceptance for the next version

Re-run the `gmd` migration from scratch against the new surface and measure:

- no raw database write anywhere in the procedure;
- no script outside the CLI, except the content extraction itself;
- under 2 minutes wall clock for ~150 entities;
- zero placeholder summaries after the run (`lint` clean on `thin_summary`);
- every `passed` criterion re-verified by `verify` against `HEAD`;
- `next` lists no feature with neither a requirement nor a path.

## 6. Open decisions for the owner

1. Field names: `description` everywhere (with R# keeping `statement`), or keep `summary` on S#/DOC?
2. Does DOC survive as a kind, or become `document` = any text cited by more than one entity?
3. Should `A#` get a body for context (I recommend no)?
4. Does the body ship in v1.2 as an additive field (`S#.body`), or is S#/DOC unified into one entity
   (a breaking change to export format and to the desktop page)?
5. `adopt` scope: proposals only (owner approves each row), or auto-write with a diff summary? I
   recommend proposals + `--apply`.

## 7. Owner direction on the body model (2026-11-23, PM analysis; wording to confirm)

Intent: entities (R#, F#, S#) carry the substantive content ("meat"), like a kanban card. DOC is
reserved for large context: the PRD, a UX review, a run report.

Rule, by role and not by size: text that **defines one entity** is that entity's body; text that
**describes the project, a period, or evidence about things** is a DOC.

Evidence from the two live stores:

- gmd: 48 of 49 documents are single-owner spec text (fold into S# bodies); only the PRD is context.
- gmd sizes: median 6.9 KB, 38 of 49 between 4 and 10 KB, 3 above 20 KB (one is 84 KB), and the PRD is
  11 KB, smaller than 40 of the specs. Size cannot separate meat from context; role can.
- hermes-product: all 5 DOCs (2 research, 2 design, 1 PRD) are context and none defines one entity.

Effect on section 6: decision 2 (DOC survives, narrowed to context) and decision 4 (body ships as an
additive optional field on R#/F#/S#; A# gets none) follow from this. The "ship create-with-body sugar
first, unify later" step in the PM review is superseded: the body belongs on the row.

Consequences to design:

1. One-time fold of gmd's 48 spec DOCs into S# bodies (a verb, not 48 hand edits). `spec` stops being a
   DOC kind for new rows; a `report` kind is proposed.
2. 10 of 29 gmd specs own 2-9 documents (S29 owns nine unrelated briefs). One body per S# means S# is
   one contract document and grouping is F#'s job; the migrator over-grouped.
3. An 84 KB body needs section reads (`doc read --section` today) and a size note in `show`;
   `list`, `trace`, `next` never print bodies.
4. With bodies on R#/F#/S#, say where design text lives: S# = contract, R# = one testable sentence,
   F# = capability boundary. Otherwise the same prose is written twice.
5. Reject `--body*` on kinds that cannot hold it (today `add S --body-file` silently drops it).

### 7.1 Status (2026-11-23)

Done, committed (6ad32f1, 3ce20ad) and synced into `~/.hermes/plugins/product`; the live store is
schema v2 (backup `product.db.bak-v1`). Core slice: `body` column on R#/F#/S#; `add --body/--body-file`
and `set body=` / `--body-file`; refusals instead of silent drops (A#, `--body-ref`, two options);
`show --body --section --max-chars` plus a size line; `list`/`trace`/`next`/dashboard payloads never
carry the text; `product_show` agent tool gains `body`, `section`, `max_chars`; S3 export/import
carries `body`. Fold: `fold-docs` (plan unless `--apply`) with the owner's two rulings (delete the
folded DOC; several DOCs of one spec become `## <doc title>` sections). Applied to gmd: 48 spec DOCs
into 29 S# bodies, only the PRD remains a DOC, ids not reused (next DOC50), 842 tests pass, bodies
verified against the sources on a rehearsal copy first. Pre-fold backup:
`~/.hermes/product-backups/20261123-pre-fold/`.

Not done: a `report` DOC kind, `next` and `thin_summary` lint, `status:`-in-body lint, `scan-prd`
over entity bodies, bodies on the desktop page, `--if-missing` / `--dry-run` / `--json`, `verify`,
`project set`, `adopt`. Open follow-up for S29: nine unrelated briefs now live as sections of one
body; splitting it into S# rows is an editing job, not a tooling one.
