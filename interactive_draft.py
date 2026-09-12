"""Interactive draft driver for running the draft via Claude Code subagents
instead of direct Anthropic API calls — no ANTHROPIC_API_KEY / credits needed.

A human (or an orchestrating Claude Code session) drives the loop:

    python interactive_draft.py context             # what decision is needed next?
    python interactive_draft.py pick <player_id> "<why>"   # record the decision
    python interactive_draft.py grade < grades.json  # attach post-draft grades (stdin)

Heuristic-controlled teams are resolved automatically inside `context` (no
decision needed from the orchestrator). LLM-controlled teams stop and wait for
a `pick`. State is persisted through engine/state.py to
state/league_<season>.json — the same file run.py's `season` command reads —
and doubles as the data source for draft_board.html's live view. Season
defaults to the current year; override with the DRAFT_SEASON env var.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime

from engine.data import load_players, draftable_players
from engine.league import Team, snake_order, ROUNDS
from engine.state import load_league, save_league, teams_from_payload, teams_to_json
from agents.base import HeuristicAgent
from config import LEAGUE

SEASON = int(os.environ.get("DRAFT_SEASON", datetime.now().year))


def _is_llm(entry: dict) -> bool:
    return entry.get("provider", "heuristic").lower() not in ("heuristic", "baseline")


def _entry_for(name: str) -> dict:
    return next(e for e in LEAGUE if e["name"] == name)


def _meta() -> dict:
    return {
        "teams": [
            {
                "name": e["name"],
                "draft_slot": i,
                "provider": e.get("provider", "heuristic"),
                "model": e.get("model", ""),
                "persona": e.get("persona", ""),
                "avatar": e.get("avatar", "🏈"),
            }
            for i, e in enumerate(LEAGUE, start=1)
        ],
        "rounds": ROUNDS,
        "num_teams": len(LEAGUE),
    }


def _load():
    payload = load_league(SEASON)
    if payload is not None:
        return teams_from_payload(payload), payload.get("overall", 1), payload.get("pick_log", [])
    teams = [Team(name=e["name"], agent=e["name"], draft_slot=i) for i, e in enumerate(LEAGUE, start=1)]
    return teams, 1, []


def _save(teams, overall, pick_log, status):
    save_league(
        SEASON,
        meta=_meta(),
        status=status,
        overall=overall,
        teams=teams_to_json(teams),
        pick_log=pick_log,
    )


def cmd_context():
    players_raw = load_players()
    pool = draftable_players(players_raw)
    pmap = {p["id"]: p for p in pool}
    teams, overall, pick_log = _load()
    slot_to_team = {t.draft_slot: t for t in teams}
    num_teams = len(teams)
    order = snake_order(num_teams, ROUNDS)
    heuristic = HeuristicAgent()

    drafted_ids = {pid for t in teams for pid in t.roster}
    available = [p for p in pool if p["id"] not in drafted_ids]

    while overall <= len(order):
        slot = order[overall - 1]
        team = slot_to_team[slot]
        rnd = (overall - 1) // num_teams + 1
        entry = _entry_for(team.name)
        ctx = {
            "round": rnd,
            "rounds": ROUNDS,
            "overall": overall,
            "available": available,
            "roster": [pmap[pid] for pid in team.roster if pid in pmap],
            "needs": team.needs(pmap),
            "have": team.positions(pmap),
        }
        if not _is_llm(entry):
            pid = heuristic.draft_pick(ctx)
            p = pmap[pid]
            team.roster.append(pid)
            available = [x for x in available if x["id"] != pid]
            pick_log.append({
                "overall": overall, "round": rnd, "slot": slot, "team": team.name,
                "player": p["name"], "pos": p["pos"], "nfl_team": p["team"], "adp": p["adp"], "why": "",
            })
            overall += 1
            continue

        _save(teams, overall, pick_log, "in_progress")
        print(json.dumps({
            "status": "awaiting_pick",
            "team": team.name,
            "model": entry.get("model"),
            "persona": entry.get("persona", ""),
            "round": rnd,
            "rounds": ROUNDS,
            "overall": overall,
            "needs": ctx["needs"],
            "roster": [p["name"] for p in ctx["roster"]],
            "available": available[:30],
        }, indent=2))
        return

    _save(teams, overall, pick_log, "complete")
    print(json.dumps({"status": "complete"}))


def cmd_pick(player_id: str, why: str = ""):
    players_raw = load_players()
    pool = draftable_players(players_raw)
    pmap = {p["id"]: p for p in pool}
    teams, overall, pick_log = _load()
    slot_to_team = {t.draft_slot: t for t in teams}
    num_teams = len(teams)
    order = snake_order(num_teams, ROUNDS)
    slot = order[overall - 1]
    team = slot_to_team[slot]
    rnd = (overall - 1) // num_teams + 1

    drafted_ids = {pid for t in teams for pid in t.roster}
    available = [p for p in pool if p["id"] not in drafted_ids]
    available_ids = {p["id"] for p in available}
    if player_id not in available_ids:
        player_id = available[0]["id"]  # safety net, mirrors engine/draft.py

    p = pmap[player_id]
    team.roster.append(player_id)
    pick_log.append({
        "overall": overall, "round": rnd, "slot": slot, "team": team.name,
        "player": p["name"], "pos": p["pos"], "nfl_team": p["team"], "adp": p["adp"], "why": why,
    })
    status = "complete" if overall >= len(order) else "in_progress"
    _save(teams, overall + 1, pick_log, status)
    print(json.dumps({"recorded": {"team": team.name, "player": p["name"], "pos": p["pos"], "why": why}}))


def cmd_grade():
    """Attach post-draft grades. Reads {"grades": [{"team","grade","summary"}, ...]}
    as JSON from stdin and merges it into the persisted state for the board to render."""
    grades = json.load(sys.stdin)["grades"]
    if load_league(SEASON) is None:
        print(json.dumps({"error": "no draft state found — run the draft first"}))
        sys.exit(1)
    save_league(SEASON, grades=grades)
    print(json.dumps({"saved_grades_for": [g["team"] for g in grades]}))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "context":
        cmd_context()
    elif sys.argv[1] == "pick":
        cmd_pick(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif sys.argv[1] == "grade":
        cmd_grade()
    else:
        print(__doc__)
