"""Agent interface for AI managers.

Each team is controlled by an Agent. An Agent must implement:
  - draft_pick(context) -> player_id
  - set_lineup(context) -> {slot: player_id}
  - waiver_claims(context) -> list of {add, drop, bid}

Two implementations are provided:
  - HeuristicAgent: no LLM, pure best-available / needs logic. Great as a
    baseline opponent and for testing the engine without API calls.
  - LLMAgent: sends the decision context to a model via a pluggable Provider
    (see agents/providers.py) and parses a strict-JSON reply. Point each team
    at a different provider/model to field models against each other.
"""
from __future__ import annotations
import json

from engine.league import FLEX_ELIGIBLE, ROSTER_SLOTS


class Agent:
    def draft_pick(self, ctx: dict) -> str:
        raise NotImplementedError

    def set_lineup(self, ctx: dict) -> dict:
        raise NotImplementedError

    def waiver_claims(self, ctx: dict) -> list:
        return []


# --------------------------------------------------------------------------
class HeuristicAgent(Agent):
    """Best-player-available, nudged by roster needs. Deterministic baseline."""

    # Max a sensible roster wants at each position (starters + reasonable bench).
    POS_CAP = {"QB": 2, "RB": 6, "WR": 6, "TE": 2, "K": 1, "DEF": 1}

    def draft_pick(self, ctx: dict) -> str:
        available = ctx["available"]        # list of player dicts, ADP-sorted
        needs = ctx["needs"]                # {pos: count_needed}
        have = ctx.get("have", {})          # {pos: count_on_roster}
        pool = list(available[:40])         # only consider a reasonable window
        # K/DEF sit far past ADP 40 in a full player pool, so they'd never
        # enter the window above even in the last two rounds. Pull the best
        # available of each in explicitly once it's time to draft them.
        if ctx["round"] >= ctx["rounds"] - 1:
            for pos in ("K", "DEF"):
                if not any(p["pos"] == pos for p in pool):
                    best_at_pos = next((p for p in available if p["pos"] == pos), None)
                    if best_at_pos:
                        pool.append(best_at_pos)
        best = None
        best_score = 1e9
        for p in pool:
            score = p["adp"]
            pos = p["pos"]
            # Hard cap: effectively skip positions we've filled to the brim.
            if have.get(pos, 0) >= self.POS_CAP.get(pos, 6):
                score += 500
            if needs.get(pos, 0) > 0:
                score -= 15                 # prioritize a starting need
            elif pos in FLEX_ELIGIBLE and needs.get("FLEX", 0) > 0:
                score -= 6
            # Don't draft K/DEF until the last two rounds; once there, a still-
            # needed K/DEF must win outright or a lower-ADP skill player (there
            # are always plenty left in a big pool) would keep beating it out.
            if pos in ("K", "DEF"):
                if ctx["round"] < ctx["rounds"] - 1:
                    score += 200
                elif needs.get(pos, 0) > 0:
                    score -= 1000
            if score < best_score:
                best_score = score
                best = p
        return (best or available[0])["id"]

    def set_lineup(self, ctx: dict) -> dict:
        """Start highest projected points per slot."""
        proj = ctx["projections"]           # {player_id: points}
        roster = ctx["roster"]              # list of player dicts
        by_pos: dict[str, list] = {}
        for p in roster:
            by_pos.setdefault(p["pos"], []).append(p)
        for lst in by_pos.values():
            lst.sort(key=lambda p: proj.get(p["id"], 0), reverse=True)

        lineup: dict[str, str] = {}
        used = set()
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            for i in range(ROSTER_SLOTS.get(pos, 0)):
                cands = [p for p in by_pos.get(pos, []) if p["id"] not in used]
                if cands:
                    lineup[f"{pos}{i+1}"] = cands[0]["id"]
                    used.add(cands[0]["id"])
        # FLEX
        flex_pool = sorted(
            [p for p in roster if p["pos"] in FLEX_ELIGIBLE and p["id"] not in used],
            key=lambda p: proj.get(p["id"], 0),
            reverse=True,
        )
        for i in range(ROSTER_SLOTS["FLEX"]):
            if i < len(flex_pool):
                lineup[f"FLEX{i+1}"] = flex_pool[i]["id"]
                used.add(flex_pool[i]["id"])
        return lineup


# --------------------------------------------------------------------------
class LLMAgent(Agent):
    """Delegates decisions to a model via a pluggable Provider.

    Any provider (Anthropic / OpenAI / Google / ...) works — see
    agents/providers.py. `persona` steers the drafting style. Falls back to
    HeuristicAgent behavior if the API call or parse fails, so a single bad
    response never stalls the league.
    """

    def __init__(self, provider, persona: str = ""):
        self.provider = provider
        self.persona = persona
        self._fallback = HeuristicAgent()

    @staticmethod
    def _parse_json(text: str):
        text = text.strip()
        if "```" in text:
            # strip a ```json ... ``` fence if present
            seg = text.split("```")[1]
            text = seg[4:].strip() if seg.lstrip().lower().startswith("json") else seg.strip()
        return json.loads(text)

    def draft_pick(self, ctx: dict) -> str:
        avail = ctx["available"][:30]
        listing = "\n".join(
            f"{p['id']}: {p['name']} {p['pos']}-{p['team']} (ADP {p['adp']})"
            for p in avail
        )
        system = (
            "You are an expert fantasy football GM in a 0.5 PPR league. "
            + (self.persona or "")
            + " ADP is a useful signal but not the whole picture. You may use web"
            " search to check recent news (injuries, depth chart moves, suspensions,"
            " role changes) that could change a player's value, then combine that"
            " with ADP and your own judgment. Don't over-search — a quick check on a"
            " borderline pick is enough. Whether or not you search, your final"
            " response — and nothing before or after it — must be ONLY a JSON"
            ' object: {"player_id": "<id>", "why": "<short>"}. Do not add commentary'
            " outside that JSON."
        )
        user = (
            f"Round {ctx['round']} of {ctx['rounds']}. Your roster needs: "
            f"{json.dumps(ctx['needs'])}.\nYour current players: "
            f"{json.dumps([p['name'] for p in ctx['roster']])}.\n"
            f"Available players:\n{listing}\n"
            "Pick ONE player_id from the list."
        )
        try:
            reply = self.provider.chat(system, user, max_tokens=1024, enable_web_search=True)
            choice = self._parse_json(reply)
            pid = str(choice["player_id"])
            if any(p["id"] == pid for p in ctx["available"]):
                return pid
        except Exception:
            pass
        return self._fallback.draft_pick(ctx)

    def set_lineup(self, ctx: dict) -> dict:
        # Lineups are mechanical; use the heuristic to avoid burning tokens.
        return self._fallback.set_lineup(ctx)
