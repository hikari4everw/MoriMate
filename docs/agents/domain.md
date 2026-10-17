# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout: single-context

This repo is **single-context**: one `CONTEXT.md` at the repo root is shared by the whole repo, and architecture decisions live in `docs/adr/`. There is no `CONTEXT-MAP.md` and no per-context `CONTEXT.md`.

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
└── ...
```

Neither `CONTEXT.md` nor `docs/adr/` exists yet. They are created lazily, the first time a term or a decision is actually resolved — not upfront. Do not create `CONTEXT-MAP.md`, and do not create per-context `CONTEXT.md` files, for this repo.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`**: read the ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
