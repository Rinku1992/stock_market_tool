"""One door for all LLM calls.

Analysts never import a vendor SDK. They get an LLMClient. Adding a second
LLM for cross-checks later means adding one more class here.
"""

import os
from typing import Protocol

from smt.store import FeatureStore

DEFAULT_MODEL = "claude-sonnet-5-5"


class LLMClient(Protocol):
    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str: ...


class ClaudeClient:
    def __init__(self, store: FeatureStore, model: str | None = None) -> None:
        import anthropic  # imported here so tests do not need a key

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self._store = store
        self.model = model or os.environ.get("SMT_CLAUDE_MODEL", DEFAULT_MODEL)

    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        kwargs = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        self._store.log_llm_call(task=task, model=self.model, system=system, prompt=prompt, response=text)
        return text


class FakeLLM:
    """For tests. Returns canned text and still writes to the recorder."""

    model = "fake"

    def __init__(self, store: FeatureStore, responses: dict[str, str] | None = None, default: str = "") -> None:
        self._store = store
        self._responses = responses or {}
        self._default = default

    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        text = self._responses.get(task, self._default)
        self._store.log_llm_call(task=task, model=self.model, system=system, prompt=prompt, response=text)
        return text
