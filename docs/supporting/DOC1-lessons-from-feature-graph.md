# DOC1 Lessons from an earlier feature-graph prototype

`kind: research | status: current | links: PRD#risks, R14, F8, S4`

**Summary.** Findings from building and testing an earlier, narrower prototype (a feature graph with state and links only) that shaped this design: what agents did and did not do, and which documents drifted.

## What was tried

A graph of a real software project (47 nodes) kept in the same kind of SQLite store, with tools and a CLI, then tested with LLM agents answering six lookup and planning questions about the project, with and without the graph.

## Findings

1. **Agents do not discover tools on their own.** With five graph tools present and never mentioned, an agent made zero graph calls in 8 runs. With a one-line hint naming the graph, it used it from its second call. Discovery needs guidance: a skill, a short prompt section, and a fixed line on every task ([R14], [S4]).
2. **On lookup questions the graph did not beat reading the repository.** Accuracy was the same and the hinted agent used more calls. The value case is planning and traceability queries (`trace`, `next`, `lint`), which were not measured and should be the first test of this tool.
3. **Status written by hand drifts.** One progress log still said a feature was off long after the configuration turned it on; a plan table showed boxes unchecked for work the board had finished. Status should be derived from evidence and live in one place ([PRD](../PRD.md#principles)).
4. **A finished card is not proof.** All cards for a lane were done while nothing had run against the real system. Hence `verified` comes only from criteria, and `evidence_level` records how strong the proof is ([S1](../specs/S1-data-model.md), [S5](../specs/S5-external-links.md)).
5. **Domains did not roll up.** A container showed "unverified" over children that were verified. Derived values must be computed in code, not typed ([S2](../specs/S2-status-and-lint.md) section 4).
6. **Per-task documents litter the repository.** Briefs, reports and review notes written into the repo duplicated what lived on the cards. The guardrail is a lint warning on such folders and a rule that task artifacts stay on the card.
7. **Backfilling invents content.** Writing requirements and criteria for an already-built system produces plausible fiction. Import features and specs first, mark them `legacy`, and add requirements and criteria when work touches them ([S6](../specs/S6-markdown-conventions.md)).
8. **Speculative write-side features were never needed.** Locks, change logs and agent write guards protect writes that agents did not make. Keep the first version read-heavy with validated writes.

## Method that worked

Parent writes a precise brief with tests first (RED then GREEN), one subagent at a time, parent re-runs the suite and reads the diff before accepting.
