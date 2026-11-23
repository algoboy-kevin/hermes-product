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

Detail (full statement, criteria text, notes, history) is returned only on request: `show R3`. Documents appear as one line with their summary; the body is read separately. An R#, F# or S# with a body shows one `body: N chars` line; `show REF --body [--section H] [--max-chars N]` reads it, bounded, with a truncation trailer (S1 section 3.1). `list`, `trace` and `next` never print a body.

## 2. Commands

| Command | Purpose |
|---|---|
| project add / list / use | Create a project, list projects, pick one. Without `--project`, the project whose `root` contains the working directory is used |
| add | Create an R#, A#, F#, S# or DOC#. `--body` / `--body-file` store the defining text on an R#, F# or S# (or the body of a DOC#); on an A# they are refused, never dropped |
| set | Change a field or status (validated) |
| link / unlink | Create or remove a relation, or add a dependency edge |
| show | Full detail for one entry |
| trace | For one entry, everything connected: requirement -> features, specs, criteria, docs, cards |
| next | What to work on next: unblocked, highest priority, dependencies met |
| lint | All checks in [S2](S2-status-and-lint.md) |
| fold-docs | One-time migration: move spec DOC text onto the S# it defines (S1 section 3.1). A plan unless `--apply`; one DOC per spec is copied verbatim, several become `## <doc title>` sections; the DOC row, links and body file are then deleted, ids never reused |
| graph | Export the feature graph as Mermaid; `--critical` prints the critical path |
| doc read | Read a DOC body: `doc read DOC4 --section "Retries" --max-chars 4000`; always bounded, reports truncation |
| scan-prd | Index bracketed references in the PRD and DOC bodies |
| export / import | See [S3](S3-text-export.md) |

Every command is also available as a tool so an agent in a session can call it directly; tool results use the same compact lines.

## 3. Agent guidance

- The plugin registers a skill describing when to call `trace`, `next` and `lint` before reading files.
- A system-prompt section of at most 600 characters appears only when the session's working directory is inside a project's `root`, naming the project and the three commands to start with. Outside a project it adds nothing and never creates or migrates the store.
- Every task given to a subagent starts with a fixed line naming the project, so the agent orients with the tools first. See [DOC1](../supporting/DOC1-lessons-from-feature-graph.md).
