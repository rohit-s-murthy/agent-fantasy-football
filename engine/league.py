"""League configuration, roster rules, and team state."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

# Starting lineup: position -> number of slots. FLEX accepts RB/WR/TE.
ROSTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 2,   # RB/WR/TE
    "K": 1,
    "DEF": 1,
}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
BENCH_SIZE = 6
ROUNDS = sum(ROSTER_SLOTS.values()) + BENCH_SIZE  # total draft rounds

SCORING_NAME = "0.5 PPR"


@dataclass
class Team:
    name: str          # human label, e.g. "Claude-Opus"
    agent: str         # agent module/key controlling this team
    draft_slot: int    # 1-indexed snake position
    roster: list[str] = field(default_factory=list)   # player_ids
    memory: dict = field(default_factory=dict)         # persistent per-team notes

    def positions(self, players: dict[str, dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for pid in self.roster:
            pos = players[pid]["pos"] if pid in players else None
            if pos:
                counts[pos] = counts.get(pos, 0) + 1
        return counts

    def needs(self, players: dict[str, dict]) -> dict[str, int]:
        """How many more of each starting slot are unfilled (rough heuristic)."""
        counts = self.positions(players)
        need = {}
        # direct slots
        for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            required = ROSTER_SLOTS.get(pos, 0)
            # FLEX raises effective RB/WR/TE demand
            need[pos] = max(0, required - counts.get(pos, 0))
        # crude flex accounting: leftover RB/WR/TE beyond base slots fill FLEX
        flex_have = 0
        for pos in FLEX_ELIGIBLE:
            flex_have += max(0, counts.get(pos, 0) - ROSTER_SLOTS.get(pos, 0))
        need["FLEX"] = max(0, ROSTER_SLOTS["FLEX"] - flex_have)
        return need


def snake_order(num_teams: int, rounds: int) -> list[int]:
    """Return the draft slot on the clock for each overall pick (1-indexed)."""
    order = []
    for r in range(rounds):
        slots = range(1, num_teams + 1)
        if r % 2 == 1:
            slots = reversed(list(slots))
        order.extend(slots)
    return order
