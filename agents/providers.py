"""Model provider abstraction.

A Provider wraps one vendor's chat API behind a single method:

    chat(system: str, user: str, max_tokens: int) -> str   # returns plain text

This lets each team be driven by any model from any vendor. Providers read
their API key from an environment variable (configurable per provider). If the
key is missing or the call fails, the caller (LLMAgent) falls back to heuristic
play, so the league always runs.

Supported out of the box:
  - AnthropicProvider   (Claude models)      env: ANTHROPIC_API_KEY
  - OpenAIProvider      (GPT models)         env: OPENAI_API_KEY
  - GoogleProvider      (Gemini models)      env: GEMINI_API_KEY
  - EchoProvider        (offline/testing; never calls out)

Adding a vendor = write one small subclass implementing `chat`.
"""
from __future__ import annotations
import json
import os
import urllib.request
from typing import Optional


class ProviderError(RuntimeError):
    pass


class Provider:
    #: default environment variable holding this provider's API key
    default_key_env: str = ""

    def __init__(self, model: str, key_env: Optional[str] = None, base_url: Optional[str] = None):
        self.model = model
        self.key_env = key_env or self.default_key_env
        self.base_url = base_url

    def _key(self) -> str:
        key = os.environ.get(self.key_env, "")
        if not key:
            raise ProviderError(f"env var {self.key_env} not set")
        return key

    @staticmethod
    def _post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def chat(self, system: str, user: str, max_tokens: int = 1024, enable_web_search: bool = False) -> str:
        raise NotImplementedError


class AnthropicProvider(Provider):
    default_key_env = "ANTHROPIC_API_KEY"
    endpoint = "https://api.anthropic.com/v1/messages"
    # Max free-standing web searches per turn before we stop letting the model dig further.
    WEB_SEARCH_MAX_USES = 3
    # Anthropic's server-side search loop caps itself at 10 rounds and returns
    # stop_reason "pause_turn"; resending resumes it. Bound our own resends too.
    MAX_RESUMES = 3

    def chat(self, system: str, user: str, max_tokens: int = 1024, enable_web_search: bool = False) -> str:
        headers = {
            "content-type": "application/json",
            "x-api-key": self._key(),
            "anthropic-version": "2023-06-01",
        }
        messages = [{"role": "user", "content": user}]
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if enable_web_search:
            # Basic (non-dynamic-filtering) variant: works uniformly across the
            # Opus/Sonnet/Haiku mix a league might field. No beta header needed.
            payload["tools"] = [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": self.WEB_SEARCH_MAX_USES}
            ]

        data = self._post(self.base_url or self.endpoint, headers, payload)
        resumes = 0
        while data.get("stop_reason") == "pause_turn" and resumes < self.MAX_RESUMES:
            messages.append({"role": "assistant", "content": data.get("content", [])})
            payload["messages"] = messages
            data = self._post(self.base_url or self.endpoint, headers, payload)
            resumes += 1
        # With tool use (e.g. web search), Claude may emit preamble text blocks
        # before searching and a separate final-answer block after. Concatenating
        # all text blocks would splice commentary into a JSON reply, so use only
        # the last one.
        text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return text_blocks[-1] if text_blocks else ""


class OpenAIProvider(Provider):
    default_key_env = "OPENAI_API_KEY"
    endpoint = "https://api.openai.com/v1/chat/completions"

    def chat(self, system: str, user: str, max_tokens: int = 1024, enable_web_search: bool = False) -> str:
        # enable_web_search is Anthropic-specific (server-side tool); ignored here.
        data = self._post(
            self.base_url or self.endpoint,
            {
                "content-type": "application/json",
                "authorization": f"Bearer {self._key()}",
            },
            {
                "model": self.model,
                "max_completion_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        return data["choices"][0]["message"]["content"]


class GoogleProvider(Provider):
    default_key_env = "GEMINI_API_KEY"
    # Gemini puts the model in the URL path.
    endpoint_tmpl = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def chat(self, system: str, user: str, max_tokens: int = 1024, enable_web_search: bool = False) -> str:
        # enable_web_search is Anthropic-specific (server-side tool); ignored here.
        url = (self.base_url or self.endpoint_tmpl).format(model=self.model)
        url = f"{url}?key={self._key()}"
        data = self._post(
            url,
            {"content-type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


class EchoProvider(Provider):
    """Offline provider used for testing. Never makes a network call; raises so
    the agent falls back to heuristic play."""

    default_key_env = "NONE"

    def chat(self, system: str, user: str, max_tokens: int = 1024, enable_web_search: bool = False) -> str:
        raise ProviderError("EchoProvider is offline by design")


# Registry so config can name a provider by string.
PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
    "echo": EchoProvider,
}


def make_provider(spec: dict) -> Provider:
    """Build a Provider from a config dict:

        {"provider": "anthropic", "model": "claude-opus-4-8",
         "key_env": "ANTHROPIC_API_KEY"}   # key_env optional
    """
    name = spec["provider"].lower()
    if name not in PROVIDERS:
        raise ProviderError(f"unknown provider '{name}'. Known: {list(PROVIDERS)}")
    cls = PROVIDERS[name]
    return cls(
        model=spec["model"],
        key_env=spec.get("key_env"),
        base_url=spec.get("base_url"),
    )
