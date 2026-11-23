# S3 Text export and import

`draft | requirements R13 | features F6`

Goal: the whole project as plain text that diffs cleanly in git, and a lossless way back.

## Layout of an export

```
<project-slug>/
  project.yaml            slug, name, root
  requirements.yaml       list of R# with fields and links
  criteria.yaml           list of A#
  features.yaml           list of F# with edges
  specs.yaml              list of S#
  docs.yaml               list of DOC# (metadata and summary)
  links.yaml              all link-table rows, sorted
  history.yaml            the log, oldest first
  docs/                   one markdown file per DOC body: DOC<id>-<slug>.md
```

## Rules

- IDs, fields and links are written in a fixed order (by ID, then field order of [S1](S1-data-model.md)), so two exports of an unchanged store are identical.
- `updated_at` is exported; export time is not.
- Import into an empty store reproduces the store exactly. Import into a non-empty store merges by ID, reports conflicts and never overwrites an existing entry.
- A DOC with `body_ref` exports the reference and hash only, not the external file.
- The export is the copy that is committed to git, next to the PRD. The SQLite file is a cache that can be rebuilt from it.
