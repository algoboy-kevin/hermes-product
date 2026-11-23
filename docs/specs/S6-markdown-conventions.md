# S6 Markdown conventions (documents-first phase)

`draft | requirements R16 | features F10`

How this repository stands in for the store until the plugin exists, so import is mechanical.

## Files and entries

| Entity | Where | Entry format |
|---|---|---|
| PRD | `docs/PRD.md` | Numbered sections with `{#anchor}` ids; referenced as `PRD.md#anchor` |
| R# | `docs/requirements.md` | `## R<n> <title>`, a header line in backticks, a `**Statement.**` paragraph |
| A# | `docs/acceptance.md` | One table row per criterion: ID, requirement, status, check |
| F# | `docs/features.md` | One table row per feature plus a mermaid graph generated from the Needs column |
| S# | `docs/specs/S<n>-<slug>.md` | First line `# S<n> <title>`, second block a header line in backticks |
| DOC# | `docs/supporting/DOC<n>-<slug>.md` | Header block with kind, status, summary; body below |

## Header lines

- R#: `` `status | priority | phase | features | specs | criteria` ``
- S#: `` `status | requirements ... | features ...` ``
- DOC#: `kind`, `status`, `summary`, `links`

## Rules

1. Every entry has a row in [`docs/index.md`](../index.md): ID, status, one-line title. The index is the registry; a reference that is not in it is broken.
2. IDs are never reused or renumbered. A retired entry stays with status `dropped`, `cut` or `superseded`.
3. Status appears only in the header line (or table), never in prose, never in a progress log or delivery index.
4. Anything longer than a paragraph that does not belong in the PRD becomes a DOC with a one-line summary.
5. When something is imported from an existing system without requirements or criteria, mark it `legacy` in its header line.
6. Check before commit: every `[X#]` reference resolves in the index, and no ID appears twice.

## Migration

When `hermes product` exists: create a project, import these files entry by entry (the header lines map one-to-one to the fields in [S1](S1-data-model.md)), run `lint`, then export and keep the export as the git copy. This repository's `docs/` then becomes the exported text, not a hand-edited source.
