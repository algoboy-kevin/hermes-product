# S4 Tool behavior (for the LLM)

`draft | requirements R2,R5,R6,R7,R10,R11,R14,R17,R18 | features F2,F4,F5,F8,F11`

## 1. Compact output

One line per item by default. Examples:

```
R3  approved  must  mvp   Pay by card at checkout   F:stripe,payments  S:7,8,9  A:2/4
F7  planned   core  mvp   Stripe integration        needs F:accounts   R:3,4    S:3
S7  approved        Use PaymentIntents with 3DS     R:3                F:stripe
DOC4 design  current  Stripe webhook flow           links F7,S7
```

Detail (full statement, criteria text, notes, history) is returned only on request: `show R3`. Documents appear as one line with their summary; the body is read separately. An R#, F# or S# with a body shows one `body: N chars` line; `show REF --body [--section H] [--max-chars N]` reads it, bounded, with a truncation trailer (S1 section 3.1). `list`, `trace` and `next` never print a body. `list --json` prints one object instead of one line per item: `ok`, `project`, `kind`, `count` and `items` (each item's `doc_links` as ref strings); without the flag the lines are unchanged.

## 2. Commands

| Command | Purpose |
|---|---|
| project add / list / use / set | Create a project, list projects, pick one, change its `name` or `root` (the slug never changes; `root=-` clears it). Without `--project`, the project whose `root` contains the working directory is used |
| add | Create an R#, A#, F#, S# or DOC#. `--body` / `--body-file` store the defining text on an R#, F# or S# (or the body of a DOC#); on an A# they are refused, never dropped |
| set | Change a field or status (validated). `--reason` gives the why for a statement change and also fills `status_reason` when the status moves to dropped, cut or superseded. `--why-not` prints every unmet prerequisite of the status move and writes nothing (exit 1 when blocked) |
| link / unlink | Create or remove a relation, or add a dependency edge. Both are idempotent: linking an existing pair or unlinking a missing one reports it on stdout and exits 0, and writes nothing. A DOC can link to a retired entry (dropped R#, cut F#, superseded S#) |
| show | Full detail for one entry |
| trace | For one entry, everything connected: requirement -> features, specs, criteria, docs, cards |
| next | What to work on next: unblocked, highest priority, dependencies met |
| lint | All checks in [S2](S2-status-and-lint.md) |
| fold-docs | One-time migration: move spec DOC text onto the S# it defines (S1 section 3.1). A plan unless `--apply`; one DOC per spec is copied verbatim, several become `## <doc title>` sections; the DOC row, links and body file are then deleted, ids never reused |
| adopt | Scan a directory for Markdown and propose one row per file: a spec file (under `specs/`, or named `S<n>-...`) becomes an S# whose body is the file text; a PRD, runbook, design, report, API or other file becomes a DOC of that kind; README, license, changelog and agent-notes files are skipped. The summary is the first prose paragraph, or `Imported from <path>` (which `thin_summary` counts). A plan unless `--apply`; rerun-safe via `adopted: <path>` in `notes` (`exists` / `changed`; `--update` refreshes changed bodies). `--only GLOB` (repeatable) limits the pass to matching paths; `--link-mentions` (with `--apply`) links each adopted DOC to every live R#/F#/S# whose summary, notes or body names its file (whole-token match, so `TESTNET-RUNBOOK.md` is not `RUNBOOK.md`; retired entries skipped; reruns link nothing twice). Never invents requirements or features, and links only by those file mentions |
| verify | Check every `passed` criterion whose evidence is `"<verbatim quote>" -- <path>:<line>`: the quote (whitespace and case ignored, at least 25 characters) must occur in that file at `--rev` (default `HEAD` in a git work tree). Verdicts: ok, drift (found far from the cited line), fail, free text (not checkable; fails only with `--strict`). Exit 1 on any fail |
| graph | Export the feature graph as Mermaid; `--critical` prints the critical path |
| doc read | Read a DOC body: `doc read DOC4 --section "Retries" --max-chars 4000`; always bounded, reports truncation |
| scan-prd | Index bracketed references in the PRD and DOC bodies |
| export / import | See [S3](S3-text-export.md) |
| apply | Run a whole batch of `add` / `set` / `link` / `unlink` from one YAML or JSON plan file in ONE process, instead of one process per operation. `plan: PLAN --project SLUG`; the project is `--project`, else the plan's `project`, else the usual resolution, and a `--project` that differs from the plan's `project` is refused |

`apply` plan format: top level `project: <slug>` (optional) and `ops: [...]`; each op is a mapping with exactly one of `add`, `set`, `link`, `unlink`. `add` takes `type` (R/A/F/S/DOC) plus the `add` flag names with underscores, `as` (an alias) and `if_missing` (default true, so a rerun is idempotent); a relative `body_file` is relative to the plan file. `set` takes `ref` plus field changes and the usual `reason` / `override_reason` / `body_file`. `link` / `unlink` take `{a, b}` or `{a, needs, optional}`. `$alias` in any ref position names an EARLIER `add ... as:`, bound to the created row or, with `if_missing`, to the existing one. The batch is validated statically, then REHEARSED on a throwaway copy of the store, then written only after the db is backed up with SQLite's backup API to `<product db>.bak-apply-<UTC stamp>`; the first bad operation refuses the whole batch (exit 1, `op <n>` named) with nothing written. The output is one `<REF> created` / `<REF> exists` line per add, one `apply <project>: <N> ops: ...` summary line, and a `backup: <path>` line when the live run happened.

Every command is also available as a tool so an agent in a session can call it directly; tool results use the same compact lines.


### Options on the writing verbs

`add`, `set`, `link`, `unlink`, `apply` and `import` take `--dry-run` (run the real verb on a throwaway copy of the store: same checks, ids and messages, each line prefixed `(dry run)`, nothing written) and `--json` (one object: `ok`, `exit`, `dry_run`, `ref`, `action`, `lines`; a refusal is `ok: false` with `error` and the same exit code). `fold-docs` takes `--json`. `add --if-missing` prints `<REF> exists` and writes nothing when the project already holds an entry with the same key: title for R#, S# and DOC# (case and whitespace ignored), slug (else title) for F#, parent requirement plus text for A#.

## 3. Agent guidance

- The plugin registers a skill describing when to call `trace`, `next` and `lint` before reading files.
- A system-prompt section of at most 600 characters appears only when the session's working directory is inside a project's `root`, naming the project and the three commands to start with. Outside a project it adds nothing and never creates or migrates the store.
- Every task given to a subagent starts with a fixed line naming the project, so the agent orients with the tools first. See [DOC1](../supporting/DOC1-lessons-from-feature-graph.md).
