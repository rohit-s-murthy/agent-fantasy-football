"""League configuration — THIS IS THE FILE YOU EDIT.

Define your field of AI managers here. Each entry is one team. Mix and match
vendors freely: Claude, GPT, Gemini, or a non-LLM heuristic baseline.

Each team entry supports:
    name      display name (must be unique)
    provider  "anthropic" | "openai" | "google" | "heuristic" | "echo"
    model     the vendor's model string (ignored for "heuristic")
    key_env   OPTIONAL: env var holding the API key. Defaults to the provider's
              standard var (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY).
              Use this to point two teams at different keys/accounts if needed.
    persona   OPTIONAL: a sentence steering that manager's drafting style.
    base_url  OPTIONAL: override the API endpoint (e.g. a proxy or Azure).
    avatar    OPTIONAL: an emoji shown on the live draft board.

Set the API keys you need before running, e.g.:
    export ANTHROPIC_API_KEY=sk-ant-...
    export OPENAI_API_KEY=sk-...
    export GEMINI_API_KEY=...

Any team whose key is missing or whose API call fails automatically falls back
to heuristic play, so the league always completes.
"""

# Draft slots are assigned in list order (team 0 -> slot 1, etc.).
#
# All-Claude field for now: only ANTHROPIC_API_KEY is available, and running
# GPT/Gemini teams without their keys would silently fall back to heuristic
# play, which would make the "model comparison" meaningless. Once
# OPENAI_API_KEY / GEMINI_API_KEY are set, swap in GPT/Gemini entries.
#
# Persona is intentionally omitted for every LLM team here: giving each model
# a different persona would confound the comparison (a behavior difference
# could be the persona being followed rather than the model itself). Add
# personas back only if the goal shifts from "compare models" to "characterful
# league" — and then give every LLM team the same one, or none at all.
LEAGUE = [
    {
        "name": "Opus-4.8",
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "avatar": "🦉",
    },
    {
        "name": "Sonnet-5",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "avatar": "🐺",
    },
    {
        "name": "Haiku-4.5",
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "avatar": "⚡",
    },
    # Non-LLM baselines so the comparison has a "dumb money" reference point.
    {"name": "Baseline-A", "provider": "heuristic", "model": "", "avatar": "🤖"},
    {"name": "Baseline-B", "provider": "heuristic", "model": "", "avatar": "🎯"},
]

# Scoring + roster rules live in engine/league.py. Change the field size just by
# adding/removing entries above (snake order adapts automatically).
