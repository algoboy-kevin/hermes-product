# Acceptance criteria

All `not_run`. Evidence level (`none|unit|integration|staging|production`) is recorded once a check has run. A requirement is `done` only when all its criteria are `passed` or `waived` ([S2](specs/S2-status-and-lint.md)).

| ID | Req | Status | Check |
|---|---|---|---|
| A1 | R1 | not_run | Given no project, when a project `shop` is added, then it exists and an R1 created in it is `R1` only in that project. |
| A2 | R1 | not_run | Given two projects, when each adds a requirement, then both are `R1` and neither can link to the other's. |
| A3 | R2 | not_run | Given a valid payload, when `add R` runs, then it prints the new ID and the entry appears in `list`. |
| A4 | R2 | not_run | Given a missing required field, when `add` runs, then it fails naming the field and creates nothing. |
| A5 | R3 | not_run | Given R1 and F1, when linked, then `trace R1` lists F1 and `trace F1` lists R1. |
| A6 | R3 | not_run | Given a retired or unknown ID, when `link` runs, then it fails and the link table is unchanged. |
| A7 | R4 | not_run | Given an `approved` requirement with no spec or criterion, when moved to `in_progress`, then it is refused naming what is missing. |
| A8 | R4 | not_run | Given a requirement with a `not_run` criterion, when moved to `done`, then it is refused. |
| A9 | R5 | not_run | Given 20 requirements, when `list R` runs, then output is exactly 20 lines and no entry exceeds one line. |
| A10 | R6 | not_run | Given R3 with features, specs, criteria and a card, when `trace R3` runs, then all four kinds are listed. |
| A11 | R7 | not_run | Given a blocked feature and an unblocked `must` one, when `next` runs, then the unblocked one is first and the blocked one is absent. |
| A12 | R8 | not_run | Given an `in_progress` requirement with no criterion, when `lint` runs, then `no_criteria` is reported as an error and the exit code is non-zero. |
| A13 | R8 | not_run | Given an `approved` requirement with no feature, when `lint` runs, then `orphan_requirement` is a warning and the exit code is zero. |
| A14 | R9 | not_run | Given F1 needs F2, when F2 is made to need F1, then the edge is rejected as a cycle. |
| A15 | R9 | not_run | Given a chain of mandatory edges, when `graph --critical` runs, then it prints the longest chain. |
| A16 | R10 | not_run | Given a DOC linked to F2, when `show F2` runs, then the DOC appears as one line with its summary and no body. |
| A17 | R10 | not_run | Given a 50 KB DOC, when `doc read DOC1 --section Retries --max-chars 4000` runs, then at most 4000 characters of that section are returned. |
| A18 | R11 | not_run | Given the PRD mentions a reference to R99 which does not exist, when `scan-prd` runs, then it reports a broken reference. |
| A19 | R12 | not_run | Given a card linked to S2, when the card moves to Done, then S2 becomes `implemented`, never `verified`. |
| A20 | R12 | not_run | Given S2 `verified`, when its card moves backward, then a warning is raised and S2 is unchanged. |
| A21 | R13 | not_run | Given a project, when exported, then rows are YAML, each body is a markdown file and the output is stable across two runs. |
| A22 | R13 | not_run | Given an export, when imported into an empty store, then a second export is byte-identical. |
| A23 | R14 | not_run | Given a session whose directory is not inside a project, then the plugin adds nothing to the prompt; inside one, it adds at most 600 characters. |
| A24 | R15 | not_run | Given a package no entry lists, when `lint --coverage` runs, then the package is reported as uncovered. |
| A25 | R16 | not_run | Given every reference in `docs/`, when checked against `docs/index.md`, then each resolves and no ID is duplicated. |
