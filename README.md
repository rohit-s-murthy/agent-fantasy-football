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
- **Snake draft** across a configurable field of agents (`engine/draft.py`),
  runnable via a funded API key (`run.py draft`) or via Claude-Code-subagent
  orchestration with no API key (`interactive_draft.py`) — see `draft_board.html`
  for a live view of a subagent-orchestrated draft in progress.
- **Weekly cycle** — pre-game lineups, real post-game scoring, and FAAB
  waivers, no hindsight (`interactive_week.py`), or hindsight-based season
  backtesting (`run.py season`).
- **Two agent types**: a deterministic `HeuristicAgent` baseline and an
  `LLMAgent` that delegates decisions to a model via the Anthropic Messages API.

## Layout
```
config.py             >>> EDIT THIS <<< define teams, models, providers, avatars
engine/data.py         data + scoring (Sleeper fetch, cache, fantasy points)
engine/league.py       roster rules, Team state, snake order, league-wide constants
engine/draft.py        snake draft engine + summary
engine/state.py        season persistence — state/league_<season>.json
engine/projection.py   non-hindsight lineup signal (season-avg, ADP fallback)
agents/base.py         Agent interface, HeuristicAgent, LLMAgent
agents/providers.py    vendor abstraction (Anthropic / OpenAI / Google / ...)
run.py                 entry point: `draft` and `season` (funded-API-key path)
interactive_draft.py   subagent-orchestrated draft (no API key needed)
interactive_week.py    subagent-orchestrated weekly cycle (no API key needed)
draft_board.html       live view of a draft in progress
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

## Weekly cycle: pre-game lineups, real scoring, FAAB waivers
`run.py season` scores each week against that week's own actual results —
useful for backtesting a past season, but it's hindsight: there's exactly one
correct lineup and no room for judgment. **`interactive_week.py`** runs the
real, no-hindsight cycle instead, driven the same way the draft was (a human
or an orchestrating Claude Code session supplies each LLM team's decision via
subagents — no `ANTHROPIC_API_KEY` needed):

```bash
python interactive_week.py context             # what needs deciding next?
python interactive_week.py waiver <team> --pass
python interactive_week.py waiver <team> --add <id> --drop <id> --bid <N>
python interactive_week.py lineup <team> '{"QB1": "<id>", "RB1": "<id>", ...}'
```

Each `context` call: scores the most recently completed week (against the
lineup that was actually locked in advance — never touches that week's own
results before it's locked), runs that week's FAAB waiver window (heuristic
teams always pass — they're the "draft once, never touch it again" control
group), then locks next week's lineup. Non-hindsight lineup signal for the
heuristic baseline is each player's season-average points so far
(`engine/projection.py`), falling back to ADP before any weeks are scored.
LLM teams get that same number plus web search for matchups/injuries — same
pattern as the draft's research step.

Sequencing is state-driven (the next unscored/unlocked week), not
calendar-driven — run `context` on whatever cadence you want (e.g. every
Wednesday, before that week's games start); nothing here computes real
dates. `run.py season` refuses to touch any week `interactive_week.py` has
already locked, so the two tools can't fight over the same week.

**Safeguard against accidental hindsight:** if a player's game has already
been played by decision time (only realistic for Week 1, since a season can
start before the draft/first cycle does), their start/bench status is forced
to match the blind ADP/season-average assignment regardless of what's
submitted — a model can't bench a known-bad performance or chase a
known-good one. Judgment only applies to players who genuinely haven't
played yet.

Not yet built: automating the weekly trigger (currently manual) and a live
board for the season (the `draft_board.html` pattern would extend naturally).

## Notes / honest limitations
- ADP (`search_rank`) is a decent draft signal but not true expert projections.
- The season-average projection used for non-locked weeks is a reasonable
  proxy, not a real projections feed — Sleeper's free API doesn't expose one.
- K scoring is approximate.
