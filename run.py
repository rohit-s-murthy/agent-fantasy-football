"""AI Fantasy League — Option B self-contained engine.

Fields a set of AI-managed teams, runs a snake draft, and (optionally)
simulates scored weeks using real Sleeper weekly stats.

Usage:
    python run.py draft                 # run a draft with the configured field
    python run.py season 2025 1 14      # score weeks 1-14 of the 2025 season
"""
from __future__ import annotations
import sys
from datetime import datetime

from engine.data import load_players, draftable_players, weekly_points
from engine.league import Team
from engine.draft import run_draft, print_draft_summary
from engine.state import load_league, save_league, teams_from_payload, teams_to_json
from agents.base import HeuristicAgent, LLMAgent
from agents.providers import make_provider
from config import LEAGUE


def build_agents() -> dict:
    """Build the Agent for each configured team, keyed by team name.

    LLM-backed teams get an LLMAgent wrapping the right provider; "heuristic"
    teams get a HeuristicAgent baseline. Independent of any drafted roster, so
    this can be rebuilt for a season command without re-drafting.
    """
    agents: dict = {}
    for entry in LEAGUE:
        name = entry["name"]
        provider_name = entry.get("provider", "heuristic").lower()
        if provider_name in ("heuristic", "baseline"):
            agent = HeuristicAgent()
        else:
            provider = make_provider(entry)
            agent = LLMAgent(provider, persona=entry.get("persona", ""))
        agents[name] = agent
    return agents


def build_field() -> tuple[list[Team], dict]:
    """Build a fresh (undrafted) league from config.py. Draft slots follow
    config order."""
    agents = build_agents()
    teams = [Team(name=e["name"], agent=e["name"], draft_slot=i) for i, e in enumerate(LEAGUE, start=1)]
    return teams, agents


def cmd_draft(season: int, force: bool = False):
    existing = load_league(season)
    if existing and not force:
        teams = teams_from_payload(existing)
        print(f"A draft for {season} already exists at state/league_{season}.json — not re-drafting.")
        print(f"Pass --force to discard it and draft again: python run.py draft {season} --force")
        print_draft_summary(teams)
        return teams

    teams, agents = build_field()
    teams, log = run_draft(teams, agents)
    print(f"Draft complete: {len(log)} picks across {len(teams)} teams.")
    print_draft_summary(teams)
    save_league(season, teams=teams_to_json(teams), pick_log=log, weekly_results={})
    print(f"\nSaved to state/league_{season}.json — `season` runs will read rosters from here all year.")
    return teams


def cmd_season(season: int, start: int, end: int, rescore: bool = False):
    payload = load_league(season)
    if not payload:
        print(f"No saved draft for {season}. Run `python run.py draft {season}` first.")
        return
    teams = teams_from_payload(payload)
    weekly_results = payload.get("weekly_results", {})
    agents = build_agents()
    players_raw = load_players()
    pmap = {p["id"]: p for p in draftable_players(players_raw)}

    for wk in range(start, end + 1):
        key = str(wk)
        if key in weekly_results and not rescore:
            continue  # already scored this week; use --rescore to redo it
        proj = weekly_points(season, wk)  # actual points as "projection" proxy
        print(f"\n--- Week {wk} ---")
        week_scores = {}
        for t in teams:
            agent = agents[t.agent]
            ctx = {
                "projections": proj,
                "roster": [pmap[pid] for pid in t.roster if pid in pmap],
            }
            lineup = agent.set_lineup(ctx)
            pts = round(sum(proj.get(pid, 0) for pid in lineup.values()), 2)
            week_scores[t.name] = pts
            print(f"  {t.name:<15} {pts:>6.2f}")
        weekly_results[key] = week_scores
        save_league(season, weekly_results=weekly_results)

    standings = {t.name: 0.0 for t in teams}
    for wk_scores in weekly_results.values():
        for name, pts in wk_scores.items():
            standings[name] += pts
    print(f"\n=== SEASON-TO-DATE TOTALS ({len(weekly_results)} week(s) scored) ===")
    for name, pts in sorted(standings.items(), key=lambda x: -x[1]):
        print(f"  {name:<15} {pts:>7.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "draft":
        rest = [a for a in sys.argv[2:] if not a.startswith("--")]
        season = int(rest[0]) if rest else datetime.now().year
        cmd_draft(season, force="--force" in sys.argv)
    elif sys.argv[1] == "season":
        rest = [a for a in sys.argv[2:] if not a.startswith("--")]
        cmd_season(int(rest[0]), int(rest[1]), int(rest[2]), rescore="--rescore" in sys.argv)
    else:
        print(__doc__)
