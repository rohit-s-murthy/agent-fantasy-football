"""Season persistence.

The draft should happen once per season and every later `season` invocation
should read the same rosters back rather than re-drafting. This module saves
and loads that state (teams/rosters + draft log + weekly results) to
`state/league_<season>.json`.
"""
from __future__ import annotations
import json
import os

from engine.league import Team

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")


def league_path(season: int) -> str:
    return os.path.join(STATE_DIR, f"league_{season}.json")


def save_league(season: int, teams: list[Team], draft_log: list, weekly_results: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    payload = {
        "season": season,
        "teams": [
            {
                "name": t.name,
                "agent": t.agent,
                "draft_slot": t.draft_slot,
                "roster": t.roster,
                "memory": t.memory,
            }
            for t in teams
        ],
        "draft_log": draft_log,
        "weekly_results": weekly_results,
    }
    tmp = league_path(season) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, league_path(season))


def load_league(season: int):
    """Return (teams, draft_log, weekly_results) or None if no saved draft exists."""
    path = league_path(season)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        payload = json.load(f)
    teams = [
        Team(
            name=t["name"],
            agent=t["agent"],
            draft_slot=t["draft_slot"],
            roster=t["roster"],
            memory=t.get("memory", {}),
        )
        for t in payload["teams"]
    ]
    return teams, payload.get("draft_log", []), payload.get("weekly_results", {})
