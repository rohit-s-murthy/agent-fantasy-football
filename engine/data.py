"""Data layer for the AI fantasy league.

Pulls the NFL player pool and weekly stats from Sleeper's free (unauthenticated)
read API and caches them locally. Also computes standard fantasy points from
raw stat lines so the engine has a self-contained scoring source of truth.
"""
from __future__ import annotations
import json
import os
import time
import urllib.request
from typing import Any

BASE = "https://api.sleeper.app/v1"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PLAYERS_CACHE = os.path.join(CACHE_DIR, "players.json")
PLAYERS_TTL = 60 * 60 * 24  # refresh player pool at most once a day

# Positions we actually draft/score.
SKILL_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def _get_json(url: str, retries: int = 3) -> Any:
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - simple retry
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed for {url}: {last}")


def load_players(force: bool = False) -> dict[str, dict]:
    """Return the full player pool keyed by player_id, cached on disk."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    fresh = (
        os.path.exists(PLAYERS_CACHE)
        and (time.time() - os.path.getmtime(PLAYERS_CACHE)) < PLAYERS_TTL
    )
    if fresh and not force:
        with open(PLAYERS_CACHE) as f:
            return json.load(f)
    data = _get_json(f"{BASE}/players/nfl")
    with open(PLAYERS_CACHE, "w") as f:
        json.dump(data, f)
    return data


def draftable_players(players: dict[str, dict]) -> list[dict]:
    """Filter to active, ranked, rosterable skill players.

    Adds a normalized `name`, `pos`, `team`, and `adp` (rank proxy) to each.
    """
    out = []
    def_rank = 190  # Sleeper never sets search_rank for DEF; slot them in late like kickers.
    for pid, p in players.items():
        pos = p.get("position")
        if pos not in SKILL_POSITIONS:
            continue
        if p.get("status") not in (None, "Active") and pos != "DEF":
            # keep DEF (no status); drop retired/inactive skill players
            if p.get("active") is False:
                continue
        rank = p.get("search_rank")
        if pos == "DEF":
            rank = def_rank
            def_rank += 1
        elif rank is None or rank > 500:
            continue
        # Drop players with no current NFL team (retired / free agents).
        if pos != "DEF" and not p.get("team"):
            continue
        # Drop anyone flagged inactive.
        if p.get("active") is False:
            continue
        out.append(
            {
                "id": pid,
                "name": p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "pos": pos,
                "team": p.get("team"),
                "adp": rank,
                "age": p.get("age"),
                "years_exp": p.get("years_exp"),
                "depth": p.get("depth_chart_order"),
                "injury": p.get("injury_status"),
            }
        )
    out.sort(key=lambda x: x["adp"])
    return out


# ---- Scoring -------------------------------------------------------------

# Standard 0.5 PPR scoring weights applied to Sleeper raw stat keys.
SCORING = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -2.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rec": 0.5,          # half PPR
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "fum_lost": -2.0,
    "fgm": 3.0,
    "xpm": 1.0,
    "pass_2pt": 2.0,
    "rush_2pt": 2.0,
    "rec_2pt": 2.0,
}


def score_line(stat: dict) -> float:
    """Compute fantasy points from a Sleeper raw stat line."""
    total = 0.0
    for key, weight in SCORING.items():
        val = stat.get(key)
        if val:
            total += val * weight
    return round(total, 2)


def weekly_stats(season: int, week: int) -> dict[str, dict]:
    """Return {player_id: raw_stat_line} for a given season/week."""
    url = f"{BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    return _get_json(url)


def current_nfl_week_for_season(season: int) -> int | None:
    """Sleeper's live 'current week' pointer, if `season` is the currently
    active NFL season — used to tell whether a week's stats are final before
    treating a partial in-progress fetch as the finished result. Returns None
    for a past season (every week is inherently already complete)."""
    state = _get_json(f"{BASE}/state/nfl")
    if str(state.get("season")) != str(season):
        return None
    return int(state["week"])


def weekly_points(season: int, week: int) -> dict[str, float]:
    """Return {player_id: fantasy_points} for a given season/week."""
    stats = weekly_stats(season, week)
    return {pid: score_line(line) for pid, line in stats.items()}


if __name__ == "__main__":
    players = load_players()
    pool = draftable_players(players)
    print(f"Loaded {len(players)} players; {len(pool)} draftable.")
    print("Top 5 by ADP:")
    for p in pool[:5]:
        print(f"  {p['adp']:>3} {p['name']} ({p['pos']}-{p['team']})")
