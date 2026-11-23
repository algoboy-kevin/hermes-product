# DOC4 Agent test results (HP-11 pilot)

`kind: research | status: current | links: R6, R7, R14, F4, F8, A23`

**Summary.** Pilot of whether `trace`, `next` and the shipped guidance help an agent answer product questions: accuracy equal to files-only, 44% more tool calls, kill rule triggered; the useful findings are the stale records and the missing PRD-goal hop.

Spec and rubric frozen BEFORE the runs: `~/.hermes/cache/scratch/hp11/frozen.md`, sha256 64843b6379f5afa27d897ff58d66e987cb7d3dc879554fcef5942a329e653137 (attached to card HP-11 t_ccd89a0d). Runner `run_hp11.py`, raw runs `runs/<arm>-<task>.json|.answer.txt`, metrics recomputed by `recompute.py` from `~/.hermes/state.db`.
Subject: deepseek-flash, one fresh `hermes -z` process per (arm, task), sequential, 12 runs. Product: project hermes-product (store imported from docs/ at f29664d). Parent scored each FINAL ANSWER against the frozen rubric. After every run: repo `git status` and a hash of the store were unchanged (12/12), no write command attempted.

## Scores (rubric max 13) and cost

| arm | T1 | T2 | T3 | T4 | total | tool calls | seconds | tokens in | tokens out | runs that used the product store |
|---|---|---|---|---|---|---|---|---|---|---|
| B  (files only)                    | 3 | 3 | 3 | 4 | 13 | 45 | 90  | 51.2k | 10.3k | n/a |
| G0 (tools present, not mentioned)  | 3 | 3 | 3 | 3* | 12 | 53 | 102 | 51.9k | 11.9k | 2 of 4 (T1 first at call 3, T3 at call 10) |
| G2 (prompt section + contract hint)| 3 | 3 | 3 | 4 | 13 | 65 | 161 | 61.4k | 17.8k | 4 of 4 (first at call 2-3) |

*G0 T4: S2 and S4 appear only inside a quoted requirement header, not stated as R7's specs; borderline, scored strictly.
Per-run calls / seconds (B, G0, G2): T1 16/30, 16/29, 21/42; T2 8/18, 9/21, 12/36; T3 12/26, 21/39, 17/47; T4 9/16, 7/13, 15/37.

## Decision rules (written before the runs)
- Q1 discovery (G0 must use the store in at least 80% of runs, else guidance is required): G0 used it in 2 of 4 (50%), and only by searching deferred tools (`tool_search` at call 2 in T1). Guidance is needed: ALREADY SHIPPED (prompt section + skill). With the guidance (G2) the store was used in 4 of 4, first call at index 2-3.
- Q2 value (G2 must beat B on accuracy, or match it with fewer calls): G2 = B on accuracy (13 = 13) and used MORE calls (65 vs 45, +44%) and time (161 s vs 90 s, 1.8x). KILL RULE TRIGGERS: keep show/trace/next as they are and stop expanding the agent surface.

## What the numbers do not say (read before using the verdict)
- n=1 per cell, one model, four lookup/planning questions, manual scoring, 13 points saturated by B. The docs are small (about 740 lines, 15 files), so reading everything is cheap: B read the whole record in 8-16 calls.
- Confounds that inflate G2: it first reads the contract skill (1 call), and the contract's report shape adds CHANGED / NOT PROVEN / DEVIATIONS sections (+7k output tokens). A fair G2 cost needs the same report shape in B.
- Tool channel mix in G2: product_* through the deferred `tool_search`/`tool_call` wrapper (T1, T4) or `hermes product` in the terminal (T2, T3). Deferred discovery works but costs 2 extra calls per run.

## Findings that matter more than the totals
1. **The records are stale against reality, and only G2 said so** (T3, unprompted): the store and docs say F1-F9 and F11 are `planned` and every criterion `not_run`, while the repo contains the working code (788 tests). G2 told the parent the first task is reconciliation, not construction. B and G0 answered from the records at face value ("build F1, then F2"). This is the real value of a trace/next surface plus the contract's "verify claims in source" rule, and it is also a to-do for us: the dogfood store needs criteria evidence and statuses brought up to date (HP-12 `sync --apply` gives implemented/in_progress for specs; criteria need `passed` + evidence).
2. **`trace A11` stops at R7/F4: the store cannot reach the PRD goal** (G2 said it explicitly). Goals are PRD prose; the hop is the `[R7]` reference inside Goal 3, and the PRD document (DOC3) is linked to nothing (lint: `orphan_doc DOC3`). Every arm still got the goal, by reading the PRD. If goal traceability matters, the model needs a goal anchor (requirement `prd_ref` is unset for all 18 requirements) or a PRD doc link.
3. Accuracy was not the differentiator: no wrong fact on any rubric item in any arm; both doc traps the rubric allowed for were not triggered (the questions asked about the records).

## Verdict
Keep `show`, `trace`, `next` and the shipped guidance; do NOT add more agent-facing verbs or tools on this evidence (kill rule). The case for the store rests on cross-checking (finding 1) and on larger or staler record sets than this pilot, not on cost: on a 740-line doc set it did not reduce calls. A follow-up worth its tokens: the same four questions on the gmd project (47 features, 28 specs, 41 dependencies), where grep is NOT cheap, and with an equalised report shape.

## Open items this raised (not done)
- Fill `prd_ref` on the requirements (or link DOC3) so `trace` reaches a PRD goal.
- Bring the dogfood store up to date (statuses, criteria evidence) so the records match the code.
- FIXED after the pilot: import-md allocated the PRD DOC id as (highest DOC<n> in the files) + 1, which would have inserted a second PRD once this DOC4 was added. A stored PRD now keeps its id on re-import (commit after f29664d, 3 tests); the real store keeps the PRD as DOC3.
