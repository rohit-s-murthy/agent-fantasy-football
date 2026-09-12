"""Season persistence — the single source of truth for a season's draft,
pick log, weekly results, and post-draft grades. Shared by run.py (the
API-key-driven path) and interactive_draft.py (the Claude-Code-subagent-driven
path), so either one can pick up where the other left off.

Everything lives in one file per season: state/league_<season>.json. Saving is
a merge — callers only pass the fields they own, and whatever else is already
on disk (e.g. a pick_log written by interactive_draft.py) is preserved.
"""
from __future__ import annotations
import json
import os

from engine.league import Team

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")


def league_path(season: int) -> str:
    return os.path.join(STATE_DIR, f"league_{season}.json")


def load_league(season: int) -> dict | None:
    """Return the raw persisted payload for a season, or None if no draft exists yet."""
    path = league_path(season)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_league(season: int, **fields) -> None:
    """Merge `fields` into the season's persisted payload and write it back.

    Only the keys passed in `fields` are overwritten — anything else already
    on disk (written by a different caller) is left untouched.
    """
    payload = load_league(season) or {}
    payload["season"] = season
    payload.update(fields)
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = league_path(season) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, league_path(season))


def teams_from_payload(payload: dict) -> list[Team]:
    return [
        Team(
            name=t["name"],
            agent=t["agent"],
            draft_slot=t["draft_slot"],
            roster=t["roster"],
            memory=t.get("memory", {}),
        )
        for t in payload["teams"]
    ]


def teams_to_json(teams: list[Team]) -> list[dict]:
    return [
        {"name": t.name, "agent": t.agent, "draft_slot": t.draft_slot, "roster": t.roster, "memory": t.memory}
        for t in teams
    ]
