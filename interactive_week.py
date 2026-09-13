"""Weekly season cycle: pre-game lineups, real scoring, FAAB waivers — driven
the same way interactive_draft.py drove the draft (Claude-Code-subagent
orchestration, no ANTHROPIC_API_KEY needed).

Sequencing is state-driven, not calendar-driven: this always operates on
"the first not-yet-scored week." Meant to be triggered daily (e.g. a
scheduled cron agent, every morning): waivers open once per week transition
(gated by `cycle.waivers_done`), but a lineup is never a one-shot lock — an
LLM team gets another chance to revise its target-week lineup once per
calendar day, right up until that week is confirmed scored, so late-breaking
injury/role news (a Saturday inactive list for Sunday's games, say) can
still be acted on. This is safe against hindsight because any player whose
game has already been played by decision time has their start/bench status
forced to the blind ADP/season-average assignment regardless of what's
submitted (see `_resolve_lineup`) — a revision can only use genuine judgment
on players who haven't played yet.

    python interactive_week.py context
        # Scores the target week once Sleeper confirms it's complete, then
        # pauses on the next LLM decision needed (a waiver call, or a team
        # that hasn't revised its lineup yet today), or reports "idle" if
        # everyone's done for today.

    python interactive_week.py waiver <team> --pass
    python interactive_week.py waiver <team> --add <id> --drop <id> --bid <N> [--why "..."]

    python interactive_week.py lineup <team> '{"QB1": "<id>", "RB1": "<id>", ...}' [--why "..."]

Heuristic teams never submit waiver claims (they're the "draft once, never
touch it again" control group); their lineup is recomputed fresh from
season-average production (engine/projection.py) every time this runs — only
LLM teams pause for input, once per day.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime

from engine.data import load_players, draftable_players, weekly_points, current_nfl_week_for_season
from engine.league import Team, ROSTER_SLOTS, FLEX_ELIGIBLE, STARTING_FAAB
from engine.state import load_league, save_league, teams_from_payload, teams_to_json
from engine.projection import build_projections
from agents.base import HeuristicAgent
from config import LEAGUE

SEASON = int(os.environ.get("WEEK_SEASON", datetime.now().year))
FREE_AGENT_LISTING_SIZE = 20


def _is_llm(entry: dict) -> bool:
    return entry.get("provider", "heuristic").lower() not in ("heuristic", "baseline")


def slot_names() -> list[str]:
    return [f"{pos}{i}" for pos, n in ROSTER_SLOTS.items() for i in range(1, n + 1)]


def _pos_family(slot: str) -> str:
    return "".join(ch for ch in slot if not ch.isdigit())


def _player_brief(p: dict, projections: dict[str, float] | None = None) -> dict:
    out = {"id": p["id"], "name": p["name"], "pos": p["pos"], "team": p["team"]}
    if p.get("injury"):
        out["injury"] = p["injury"]
    if projections is not None:
        out["season_avg"] = round(projections.get(p["id"], 0.0), 2)
    return out


def _resolve_lineup(roster_players: list[dict], submitted: dict, projections: dict[str, float], locked_ids: frozenset[str] = frozenset()) -> dict[str, str]:
    """Take a subagent's slot->player_id submission, keep whatever validates
    (roster membership + position eligibility, first-come on duplicates), and
    patch any unfilled slots from a heuristic fallback over the leftover
    players — so a partial or malformed submission still yields a complete,
    legal lineup.

    `locked_ids` are players whose game for this week has already been
    played by decision time (should only ever be non-empty for Week 1, given
    the draft landed after kickoff). Their start/bench status is forced to
    match the blind ADP/season-average assignment regardless of what's
    submitted, so a model can't bench a known-bad performance or chase a
    known-good one — the submission only governs players who haven't played
    yet.
    """
    pmap = {p["id"]: p for p in roster_players}
    lineup: dict[str, str] = {}
    counters = {pos: 0 for pos in ROSTER_SLOTS}

    def try_place(base: str, pid: str) -> bool:
        if counters[base] >= ROSTER_SLOTS[base] or pid in lineup.values():
            return False
        counters[base] += 1
        lineup[f"{base}{counters[base]}"] = pid
        return True

    blind_lineup = HeuristicAgent().set_lineup({"projections": projections, "roster": roster_players})
    blind_starters = set(blind_lineup.values())

    # Already-played starters (per the blind assignment) are locked in first,
    # claiming their natural slot before anything else is placed.
    for slot, pid in blind_lineup.items():
        if pid in locked_ids:
            try_place(_pos_family(slot), pid)

    # direct-position slots first, then FLEX, so a submitted RB fills RB
    # before spilling into FLEX
    for pass_bases in (set(ROSTER_SLOTS) - {"FLEX"}, {"FLEX"}):
        for slot, pid in (submitted or {}).items():
            base = _pos_family(slot)
            if base not in pass_bases or pid not in pmap or pid in lineup.values():
                continue
            if pid in locked_ids and pid not in blind_starters:
                continue  # already played and blind-benched -- can't be started
            p_pos = pmap[pid]["pos"]
            eligible = p_pos in FLEX_ELIGIBLE if base == "FLEX" else p_pos == base
            if eligible:
                try_place(base, pid)

    remaining = [
        p for p in roster_players
        if p["id"] not in lineup.values() and not (p["id"] in locked_ids and p["id"] not in blind_starters)
    ]
    fallback = HeuristicAgent().set_lineup({"projections": projections, "roster": remaining})
    fallback_by_base: dict[str, list[str]] = {}
    for fb_slot, fb_pid in fallback.items():
        fallback_by_base.setdefault(_pos_family(fb_slot), []).append(fb_pid)

    for pos, n in ROSTER_SLOTS.items():
        for i in range(1, n + 1):
            key = f"{pos}{i}"
            if key in lineup:
                continue
            pool = fallback_by_base.get(pos, [])
            if pool:
                lineup[key] = pool.pop(0)

    return lineup


def _already_played_ids(week: int) -> frozenset[str]:
    """Player IDs with an existing stat line for this week — i.e. their game
    has already happened. Only the *presence* of a stat line is used; the
    actual point values are never surfaced to a lineup decision, to avoid
    leaking the outcome. Expected to be empty for any week decided genuinely
    in advance; only Week 1 (decided after the season already kicked off)
    should ever see this be non-empty."""
    return frozenset(weekly_points(SEASON, week).keys())


def _all_team_names() -> list[str]:
    return [e["name"] for e in LEAGUE]


def _today_str() -> str:
    return datetime.utcnow().date().isoformat()


def _target_week(weekly_results: dict) -> int:
    """The week currently being worked on: the first one not yet scored.
    Weeks before it are scored by definition; the week itself may have no
    lineups at all yet (bootstrap), a partial set, or a fully day-refreshed
    set — none of that matters for *which* week is the target, only whether
    it's been scored."""
    week = 1
    while str(week) in weekly_results:
        week += 1
    return week


def _week_confirmed_complete(wk: int) -> bool:
    """True once Sleeper's live week pointer has moved past `wk` (or `SEASON`
    isn't the currently active season, meaning every week is inherently
    already over). False — conservatively — if the check itself fails."""
    try:
        active_week = current_nfl_week_for_season(SEASON)
    except Exception:
        return False
    return active_week is None or wk < active_week


def _score_pending_weeks(payload: dict) -> bool:
    """If the target week is confirmed complete, score it and advance.
    A week still in progress is left alone — scoring it now would lock in a
    partial, misleadingly-low result for anyone whose players haven't played
    yet. Any team that never got a lineup submitted this week (e.g. a missed
    routine run) falls back to a fresh heuristic lineup so scoring never
    stalls on a missing submission. Fully mechanical, never pauses."""
    weekly_results = payload.setdefault("weekly_results", {})
    player_week_scores = payload.setdefault("player_week_scores", {})
    locked_lineups = payload.setdefault("locked_lineups", {})
    week = _target_week(weekly_results)
    if not _week_confirmed_complete(week):
        return False

    week_key = str(week)
    week_lineups = locked_lineups.setdefault(week_key, {})
    teams = teams_from_payload(payload)
    pool = draftable_players(load_players())
    pmap = {p["id"]: p for p in pool}
    for t in teams:
        if t.name in week_lineups:
            continue
        roster_players = [pmap[pid] for pid in t.roster if pid in pmap]
        projections = build_projections(roster_players, player_week_scores)
        week_lineups[t.name] = HeuristicAgent().set_lineup({"projections": projections, "roster": roster_players})

    proj = weekly_points(SEASON, week)
    player_week_scores[week_key] = proj
    weekly_results[week_key] = {
        name: round(sum(proj.get(pid, 0) for pid in lineup.values()), 2)
        for name, lineup in week_lineups.items()
    }
    save_league(SEASON, weekly_results=weekly_results, player_week_scores=player_week_scores, locked_lineups=locked_lineups)
    return True


def _resolve_waiver_phase(payload: dict, teams_by_name: dict[str, Team], week: int, free_agent_ids: set[str]) -> None:
    """Highest bid wins a contested free agent; losing claims cost nothing.
    Ties broken by team name for determinism. Invalid claims (bad drop,
    over budget) simply lose, logged for transparency."""
    submissions = payload.get("cycle", {}).get("waiver_submissions", {})
    faab = payload.setdefault("faab_budget", {})
    for name in teams_by_name:
        faab.setdefault(name, STARTING_FAAB)
    transactions = payload.setdefault("transactions", [])

    claims = [
        (name, sub["add"], sub.get("drop"), int(sub.get("bid", 0)), sub.get("why", ""))
        for name, sub in submissions.items()
        if sub != "pass" and sub
    ]
    claims.sort(key=lambda c: (-c[3], c[0]))  # highest bid first, then team name

    awarded: set[str] = set()
    for name, add_id, drop_id, bid, why in claims:
        team = teams_by_name[name]
        won = (
            add_id in free_agent_ids
            and add_id not in awarded
            and 0 <= bid <= faab.get(name, 0)
            and drop_id in team.roster
        )
        if won:
            awarded.add(add_id)
            team.roster.remove(drop_id)
            team.roster.append(add_id)
            faab[name] -= bid
        transactions.append({"week": week, "team": name, "add": add_id, "drop": drop_id, "bid": bid, "won": won, "why": why})

    save_league(SEASON, teams=teams_to_json(list(teams_by_name.values())), faab_budget=faab, transactions=transactions)


def cmd_context():
    payload = load_league(SEASON)
    if payload is None:
        print(json.dumps({"error": f"No draft found for {SEASON}. Run the draft first."}))
        return

    _score_pending_weeks(payload)
    payload = load_league(SEASON)  # re-read after scoring may have saved

    teams = teams_from_payload(payload)
    teams_by_name = {t.name: t for t in teams}
    pool = draftable_players(load_players())
    pmap = {p["id"]: p for p in pool}
    player_week_scores = payload.get("player_week_scores", {})
    locked_lineups = payload.get("locked_lineups", {})
    weekly_results = payload.get("weekly_results", {})

    next_week = _target_week(weekly_results)
    llm_names = [e["name"] for e in LEAGUE if _is_llm(e)]
    heuristic_names = [e["name"] for e in LEAGUE if not _is_llm(e)]

    cycle = payload.get("cycle", {})
    if cycle.get("week") != next_week:
        cycle = {"week": next_week, "waiver_submissions": {}, "waivers_done": next_week == 1}

    # --- Waiver phase (skipped entirely before week 1 — nothing to base it on) ---
    if not cycle.get("waivers_done"):
        submissions = cycle.get("waiver_submissions", {})
        missing = [n for n in llm_names if n not in submissions]
        if missing:
            drafted_ids = {pid for t in teams for pid in t.roster}
            free_agents = [p for p in pool if p["id"] not in drafted_ids][:FREE_AGENT_LISTING_SIZE]
            name = missing[0]
            team = teams_by_name[name]
            roster_players = [pmap[pid] for pid in team.roster if pid in pmap]
            projections = build_projections(roster_players, player_week_scores)
            save_league(SEASON, cycle=cycle)
            print(json.dumps({
                "status": "awaiting_waiver",
                "team": name,
                "week": next_week,
                "budget": payload.get("faab_budget", {}).get(name, STARTING_FAAB),
                "roster": [_player_brief(p, projections) for p in roster_players],
                "free_agents": [_player_brief(p) for p in free_agents],
            }, indent=2))
            return
        drafted_ids = {pid for t in teams for pid in t.roster}
        free_agent_ids = {p["id"] for p in pool if p["id"] not in drafted_ids}
        _resolve_waiver_phase(payload, teams_by_name, next_week, free_agent_ids)
        payload = load_league(SEASON)
        teams = teams_from_payload(payload)
        teams_by_name = {t.name: t for t in teams}
        cycle = {"week": next_week, "waiver_submissions": {}, "waivers_done": True}
        save_league(SEASON, cycle=cycle)

    # --- Lineup phase: heuristic teams are recomputed fresh every call (cheap,
    # idempotent, always reflects the latest season-average signal); LLM teams
    # are asked at most once per calendar day for the target week, so a
    # revision stays available right up until the week is confirmed scored.
    week_key = str(next_week)
    week_lineups = locked_lineups.get(week_key, {})
    today = _today_str()
    lineup_daily = payload.get("lineup_daily", {})
    if lineup_daily.get("week") != next_week or lineup_daily.get("date") != today:
        lineup_daily = {"week": next_week, "date": today, "updated": []}

    for name in heuristic_names:
        team = teams_by_name[name]
        roster_players = [pmap[pid] for pid in team.roster if pid in pmap]
        projections = build_projections(roster_players, player_week_scores)
        week_lineups[name] = HeuristicAgent().set_lineup({"projections": projections, "roster": roster_players})
    locked_lineups[week_key] = week_lineups
    save_league(SEASON, locked_lineups=locked_lineups)

    missing = [n for n in llm_names if n not in lineup_daily.get("updated", [])]
    if missing:
        name = missing[0]
        team = teams_by_name[name]
        roster_players = [pmap[pid] for pid in team.roster if pid in pmap]
        projections = build_projections(roster_players, player_week_scores)
        locked_ids = _already_played_ids(next_week) & {p["id"] for p in roster_players}
        save_league(SEASON, lineup_daily=lineup_daily, cycle=cycle)
        payload_out = {
            "status": "awaiting_lineup",
            "team": name,
            "week": next_week,
            "date": today,
            "current_lineup": week_lineups.get(name),
            "roster": [_player_brief(p, projections) for p in roster_players],
            "slots_needed": slot_names(),
        }
        if locked_ids:
            payload_out["already_played"] = sorted(locked_ids)
            payload_out["note"] = (
                "The players listed in already_played have already played this week (their game "
                "already happened) — don't bother researching them, and don't try to bench or start "
                "them based on anything you find; your choice for them will be overridden to match a "
                "blind ADP-based assignment either way. Focus your judgment on everyone else."
            )
        print(json.dumps(payload_out, indent=2))
        return

    save_league(SEASON, lineup_daily=lineup_daily, cycle=cycle)
    standings = {}
    for wk_scores in weekly_results.values():
        for name, pts in wk_scores.items():
            standings[name] = round(standings.get(name, 0) + pts, 2)
    print(json.dumps({
        "status": "idle",
        "week_in_progress": next_week,
        "week_confirmed_complete": _week_confirmed_complete(next_week),
        "scored_through": max((int(w) for w in weekly_results), default=0),
        "standings": dict(sorted(standings.items(), key=lambda kv: -kv[1])),
        "faab_budget": payload.get("faab_budget", {}),
    }, indent=2))


def cmd_waiver(team_name: str, add_id: str | None = None, drop_id: str | None = None, bid: int = 0, passed: bool = False, why: str = ""):
    payload = load_league(SEASON)
    if payload is None:
        print(json.dumps({"error": f"No draft found for {SEASON}."}))
        return
    next_week = _target_week(payload.get("weekly_results", {}))
    cycle = payload.get("cycle", {})
    if cycle.get("week") != next_week:
        cycle = {"week": next_week, "waiver_submissions": {}, "waivers_done": next_week == 1}
    if cycle.get("waivers_done"):
        print(json.dumps({"error": f"No waiver window open for week {next_week} (already resolved or not applicable)."}))
        return
    submissions = cycle.setdefault("waiver_submissions", {})
    submissions[team_name] = "pass" if passed else {"add": add_id, "drop": drop_id, "bid": bid, "why": why}
    save_league(SEASON, cycle=cycle)
    print(json.dumps({"recorded_waiver": {"team": team_name, "week": next_week, "decision": submissions[team_name]}}))


def cmd_lineup(team_name: str, lineup_json: str, why: str = ""):
    payload = load_league(SEASON)
    if payload is None:
        print(json.dumps({"error": f"No draft found for {SEASON}."}))
        return
    teams = teams_from_payload(payload)
    team = next((t for t in teams if t.name == team_name), None)
    if team is None:
        print(json.dumps({"error": f"Unknown team {team_name}"}))
        return
    pool = draftable_players(load_players())
    pmap = {p["id"]: p for p in pool}
    roster_players = [pmap[pid] for pid in team.roster if pid in pmap]
    projections = build_projections(roster_players, payload.get("player_week_scores", {}))

    next_week = _target_week(payload.get("weekly_results", {}))
    locked_ids = _already_played_ids(next_week) & {p["id"] for p in roster_players}
    lineup = _resolve_lineup(roster_players, json.loads(lineup_json), projections, locked_ids)
    locked_lineups = payload.get("locked_lineups", {})
    week_lineups = locked_lineups.setdefault(str(next_week), {})
    week_lineups[team_name] = lineup
    lineup_log = payload.get("lineup_log", [])
    today = _today_str()
    lineup_log.append({"week": next_week, "team": team_name, "why": why, "date": today})

    lineup_daily = payload.get("lineup_daily", {})
    if lineup_daily.get("week") != next_week or lineup_daily.get("date") != today:
        lineup_daily = {"week": next_week, "date": today, "updated": []}
    if team_name not in lineup_daily["updated"]:
        lineup_daily["updated"].append(team_name)

    save_league(SEASON, locked_lineups=locked_lineups, lineup_log=lineup_log, lineup_daily=lineup_daily)
    print(json.dumps({"locked_lineup": {"team": team_name, "week": next_week, "lineup": lineup}}))


def _parse_flags(args: list[str]) -> dict:
    flags: dict = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            i += 1
    return flags


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "context":
        cmd_context()
    elif sys.argv[1] == "waiver":
        flags = _parse_flags(sys.argv[3:])
        if flags.get("pass"):
            cmd_waiver(sys.argv[2], passed=True)
        else:
            cmd_waiver(sys.argv[2], add_id=flags.get("add"), drop_id=flags.get("drop"), bid=int(flags.get("bid", 0)), why=flags.get("why", ""))
    elif sys.argv[1] == "lineup":
        flags = _parse_flags(sys.argv[4:])
        cmd_lineup(sys.argv[2], sys.argv[3], why=flags.get("why", ""))
    else:
        print(__doc__)
