# hermes-product

Product documents for LLM agents: one place for the PRD, requirements, features, specs,
acceptance criteria and supporting documents, linked so an agent can always answer
**what must be built, why, how, and whether it is done**. Code repositories then hold only
code, a README and config.

> **Status: documents first.** This repository currently *is* the product, written as plain
> markdown in the same model the tool will use. When the `hermes product` plugin exists, these
> files are imported into it and the plugin becomes the source of truth. Nothing here is
> implemented yet.

## The model

| ID | Entity | Answers |
|---|---|---|
| (PRD) | Product requirements document | What is this product and why? One per project, markdown. |
| R# | Requirement | What must it do? |
| A# | Acceptance criterion | How do we prove a requirement is done? |
| F# | Feature | Which capability delivers it, and in what build order? |
| S# | Spec | How exactly is it built or must it behave? |
| DOC# | Supporting document | Reference material too big for the PRD or a kanban card. |

```
PRD -> R# requirements <-> F# features <-> S# specs
            |                  |              |
           A# criteria     dependency graph   kanban cards (external links)
```

## Layout

```
README.md
docs/
  PRD.md              the main document
  requirements.md     R#, each with its A# criteria ids
  acceptance.md       A# index and status
  features.md         F# and the dependency graph
  specs/              S1..S6, one file each
  supporting/         DOC#, long reference material
  index.md            registry: every ID, one line each (stands in for `hermes product list`)
```

## Reading order

1. [`docs/PRD.md`](docs/PRD.md): what and why
2. [`docs/requirements.md`](docs/requirements.md) then [`docs/features.md`](docs/features.md)
3. [`docs/specs/S1-data-model.md`](docs/specs/S1-data-model.md): the data model everything rests on
4. [`docs/index.md`](docs/index.md): find anything by ID

## Conventions while this is markdown

- Reference by ID in text: `[R3]`, `[F7]`, `[S2]`, `[A12]`, `[DOC1]`. Every reference must resolve to
  an entry in [`docs/index.md`](docs/index.md); IDs are never reused or renumbered.
- Status lives only in the entry's header line, never in prose or in a progress log.
- Long text goes in a DOC# under `docs/supporting/`, with a one-line summary in the registry.
- Full rules: [`docs/specs/S6-markdown-conventions.md`](docs/specs/S6-markdown-conventions.md).

## License

MIT
