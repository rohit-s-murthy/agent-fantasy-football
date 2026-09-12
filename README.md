# AI Fantasy Football League (self-contained engine)

A league where every team is run by an AI. This is **Option B**: a self-contained
engine that does not depend on Sleeper's (read-only) draft/lineup mechanics. It
pulls the real NFL player pool and real weekly stats from Sleeper's free API,
then runs its own draft, lineups, and scoring — so models have full autonomy with
zero execution friction.

## Why not just use Sleeper directly?
Sleeper has no *write* API — you can't submit picks, lineups, or waivers through
it programmatically. Rather than drive the app with fragile browser automation
(which also violates Sleeper's terms), this engine owns the game state itself and
uses Sleeper purely as a **data source**.

## What works today
- **Player pool + ADP** from `players/nfl` (cached daily in `data/`).
- **Real scoring** from `stats/nfl/regular/{season}/{week}` — 0.5 PPR weights in
  `engine/data.py::SCORING`.
- **Snake draft** across a configurable field of agents (`engine/draft.py`).
- **Weekly lineup optimization + season standings** (`run.py season`).
- **Two agent types**: a deterministic `HeuristicAgent` baseline and an
  `LLMAgent` that delegates decisions to a model via the Anthropic Messages API.

## Layout
```
config.py          >>> EDIT THIS <<< define teams, models, providers, personas
engine/data.py     data + scoring (Sleeper fetch, cache, fantasy points)
engine/league.py   roster rules, Team state, snake order
engine/draft.py    snake draft engine + summary
agents/base.py     Agent interface, HeuristicAgent, LLMAgent
agents/providers.py vendor abstraction (Anthropic / OpenAI / Google / ...)
run.py             entry point: `draft` and `season` commands
```

## Run it
```bash
python run.py draft                 # draft for the current year (once), saved to state/
python run.py draft 2026            # draft for a specific season explicitly
python run.py draft 2026 --force    # discard the saved draft and redraft
python run.py season 2026 1 1       # score week 1, print season-to-date standings
python run.py season 2026 1 14      # score any not-yet-scored weeks in 1-14
python run.py season 2026 1 1 --rescore  # redo a week even if already scored
```
The draft runs **once** per season and is saved to `state/league_<season>.json`
(rosters, draft log, and weekly results). `season` commands load that file
instead of re-drafting, and only score weeks not already recorded — so you can
run `season` weekly all year and it accumulates standings incrementally.

## Configure each team's model (the main thing you'll edit)
Open **`config.py`** and edit the `LEAGUE` list. One entry = one team. Mix
vendors freely:

```python
LEAGUE = [
    {"name": "Opus-4.8",  "provider": "anthropic", "model": "claude-opus-4-8"},
    {"name": "Sonnet-5",  "provider": "anthropic", "model": "claude-sonnet-5"},
    {"name": "GPT-5",     "provider": "openai",    "model": "gpt-5"},
    {"name": "Gemini",    "provider": "google",    "model": "gemini-2.5-pro"},
    {"name": "Baseline",  "provider": "heuristic", "model": ""},
]
```

Per-entry options: `name`, `provider`, `model`, and optional `persona`
(steers drafting style), `key_env` (point a team at a specific API-key env var),
and `base_url` (custom endpoint/proxy/Azure). Draft slots follow list order;
add or remove entries to change field size — snake order adapts automatically.

### Providers and keys
| provider    | env var for key      | example model         |
|-------------|----------------------|-----------------------|
| `anthropic` | `ANTHROPIC_API_KEY`  | `claude-opus-4-8`     |
| `openai`    | `OPENAI_API_KEY`     | `gpt-5`               |
| `google`    | `GEMINI_API_KEY`     | `gemini-2.5-pro`      |
| `heuristic` | (none)               | — baseline, no API    |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
```
Any team whose key is missing or whose call fails **falls back to heuristic
play automatically**, so the league always completes. Adding a new vendor is
one small subclass in `agents/providers.py`.

## "Each AI handles the whole season" — how to extend
- **Persistent memory:** `Team.memory` is a dict meant to be written to
  `state/<team>.json` after each week so a model remembers its own strategy.
- **Waivers/FAAB:** `Agent.waiver_claims` is stubbed; wire it into a weekly loop
  that diffs rostered vs. available players and lets each model bid.
- **Trades:** add `propose_trades` / `respond_to_trade` to the Agent interface
  and run a negotiation round between weeks.
- **Projections:** `run season` currently uses *actual* points as the lineup
  signal (hindsight). For a fair game, swap in a projections source or a
  pre-week model estimate so lineups are set without knowing outcomes.

## Notes / honest limitations
- ADP (`search_rank`) is a decent draft signal but not true expert projections.
- Lineup-setting uses actual points as a stand-in for projections; replace with
  real projections before treating standings as "skill".
- Defense (DEF) scoring is not yet modeled (Sleeper DEF stats need a separate
  mapping); K scoring is approximate.
