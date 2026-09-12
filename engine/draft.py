"""Snake draft engine: runs a full draft across a field of AI agents."""
from __future__ import annotations
import json
import os
from datetime import datetime

from engine.data import load_players, draftable_players
from engine.league import Team, ROUNDS, snake_order

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")


def run_draft(teams: list[Team], agents: dict, rounds: int = ROUNDS, log: bool = True):
    """Execute a snake draft. `agents` maps team.agent -> Agent instance.

    Returns the drafted teams (roster lists populated) and a pick log.
    """
    players_raw = load_players()
    pool = draftable_players(players_raw)
    pmap = {p["id"]: p for p in pool}
    available = list(pool)
    num_teams = len(teams)
    slot_to_team = {t.draft_slot: t for t in teams}
    order = snake_order(num_teams, rounds)

    pick_log = []
    for overall, slot in enumerate(order, start=1):
        team = slot_to_team[slot]
        rnd = (overall - 1) // num_teams + 1
        ctx = {
            "round": rnd,
            "rounds": rounds,
            "overall": overall,
            "available": available,
            "roster": [pmap[pid] for pid in team.roster if pid in pmap],
            "needs": team.needs(pmap),
            "have": team.positions(pmap),
        }
        agent = agents[team.agent]
        pid = agent.draft_pick(ctx)
        if pid not in pmap or pid not in {p["id"] for p in available}:
            pid = available[0]["id"]  # safety
        team.roster.append(pid)
        available = [p for p in available if p["id"] != pid]
        p = pmap[pid]
        pick_log.append(
            {
                "overall": overall,
                "round": rnd,
                "slot": slot,
                "team": team.name,
                "player": p["name"],
                "pos": p["pos"],
                "nfl_team": p["team"],
                "adp": p["adp"],
            }
        )

    if log:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(LOG_DIR, f"draft_{stamp}.json"), "w") as f:
            json.dump(pick_log, f, indent=2)
    return teams, pick_log


def print_draft_summary(teams: list[Team]):
    players_raw = load_players()
    pmap = {p["id"]: p for p in draftable_players(players_raw)}
    for t in sorted(teams, key=lambda x: x.draft_slot):
        print(f"\n=== {t.name} (slot {t.draft_slot}) ===")
        for pid in t.roster:
            p = pmap.get(pid, {"name": pid, "pos": "?", "team": "?"})
            print(f"  {p['pos']:>3}  {p['name']} ({p['team']})")
