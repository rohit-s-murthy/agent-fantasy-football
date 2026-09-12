"""Non-hindsight lineup signal for HeuristicAgent (and as one input for LLM
teams alongside their own research).

`run.py season` fed lineup-setting that week's own actual final score, which
has exactly one correct answer and leaves no room for judgment. This module
gives a legitimate substitute: each player's average points across weeks
they actually have a stat line for so far this season, falling back to an
ADP-based proxy before any weeks are scored. Never looks at the week being
projected for.
"""
from __future__ import annotations


def player_projection(player_id: str, adp: float, player_week_scores: dict[str, dict[str, float]]) -> float:
    """Mean points across already-scored weeks the player has a stat line
    for. A week with no entry for this player means they didn't play (bye,
    inactive) and is excluded rather than counted as zero."""
    played = [wk[player_id] for wk in player_week_scores.values() if player_id in wk]
    if played:
        return sum(played) / len(played)
    return max(0.0, 200 - adp)  # week-1 fallback: better ADP -> higher proxy


def build_projections(roster_players: list[dict], player_week_scores: dict[str, dict[str, float]]) -> dict[str, float]:
    return {p["id"]: player_projection(p["id"], p["adp"], player_week_scores) for p in roster_players}
