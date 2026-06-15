# AGENTS.md — read first, every task

Fantasy Football Decision Maker pulls Sleeper league data + NFL stats into local JSON snapshots, then scores players against a user-chosen risk profile via CLI. This file is a **map**, not a manual.

## Core beliefs

1. **`docs/` is a context map.** Code is the source of truth. Docs map the system's shape — stages, contracts, deferred decisions, and undocumented Sleeper endpoints we've discovered. Update a doc when the map changes; don't open doc PRs for routine code edits. If no doc covers a newly-mapped area, add one and link it from [`docs/index.md`](docs/index.md).
2. **Snapshots are immutable.** Every batch run writes a *new* dated folder under `data/snapshots/`. Never mutate or delete an existing snapshot. The decision engine always reads the lexicographically latest. This makes runs reproducible and lets us diff weeks trivially.
3. **Never invent fantasy logic.** The author of this repo does not play fantasy football. If a fact about scoring, positions, eligibility, or league mechanics is not in `docs/`, not in the Sleeper API response, and not in the conversation, **ask** — don't guess. The glossary at [`docs/references/fantasy-glossary.md`](docs/references/fantasy-glossary.md) is the load-bearing source for terms.
4. **Quarantine over drop.** If a player or game returns malformed data, log structured and skip *that record*, never silently absorb whole responses. `try/except: pass` is forbidden.
5. **Rules are mechanical or they don't exist.** Layering, lint, types — enforced by `import-linter`, `ruff`, `mypy`. A rule that lives only in a doc is a wish.

## Operating rules

1. **Read this file before every task.** It points at the right deeper doc; it is not the doc.
2. **Service-local `AGENTS.md` overrides this one.** Each `services/<name>/` ships its own short `AGENTS.md`. Read it before working in that directory.
3. **Don't invent context.** If a fact is not in `docs/`, not in the code, and not in the conversation, ask.
4. **Sleeper docs lie by omission.** Several endpoints we depend on are not in the official docs but work. Every endpoint we use — documented or not — is recorded in [`docs/references/sleeper-api.md`](docs/references/sleeper-api.md). If you discover a new one, add it. If a known one breaks, note the date and failure mode.

## Where to find things

| Question | Go to |
| -- | -- |
| What's in the repo? | [`docs/index.md`](docs/index.md) |
| How is the system layered? | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Reliability / security bars | [`docs/RELIABILITY.md`](docs/RELIABILITY.md), [`docs/SECURITY.md`](docs/SECURITY.md) |
| What Sleeper endpoints exist? | [`docs/references/sleeper-api.md`](docs/references/sleeper-api.md) |
| What does "PPR / FLEX / variance" mean? | [`docs/references/fantasy-glossary.md`](docs/references/fantasy-glossary.md) |

## What to load when

| Task shape | Load |
| -- | -- |
| Quick question / orientation | This file + [`ARCHITECTURE.md`](ARCHITECTURE.md). Stop there. |
| Small fix in an existing service | The file you're touching + that service's `AGENTS.md`. |
| Touching the scoring math | [`docs/services/decision-engine.md`](docs/services/decision-engine.md) + [`docs/product-specs/milestone-2/2.2-scoring-model.md`](docs/product-specs/milestone-2/2.2-scoring-model.md). |
| Touching Sleeper API calls | [`docs/references/sleeper-api.md`](docs/references/sleeper-api.md) first. |
| Anything fantasy-domain | [`docs/references/fantasy-glossary.md`](docs/references/fantasy-glossary.md). |

## How to work

- Use tooling and conventions already present. Don't introduce new libraries or patterns unless a doc authorises it.
- If something is missing (a tool, a lint, a doc), add it — don't work around it.
- Before opening a PR: relevant docs updated; lint and tests pass; PR description lists which docs were touched and why.
