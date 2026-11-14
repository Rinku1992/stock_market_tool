"""One door for all LLM calls.

Analysts never import a vendor SDK. They get an LLMClient. Adding a second
LLM for cross-checks later means adding one more class here.

Every client goes through the same budget guard (GuardedClient.complete):

1. Cache: the same model, system prompt and prompt answered before -> the stored answer, no API call.
   Cache hits are recorded with cache_hit = true and 0 tokens.
2. Estimate the call: input ~ characters / 3 (rounded up, +10), output = max_tokens (the most it can use).
3. Dry run (SMT_LLM_DRY_RUN=1): print task, model and estimate; no API call, nothing recorded.
4. Caps (input + output tokens, real calls only): SMT_LLM_MAX_TOKENS_PER_RUN (default 100,000, per
   client, which is one engine run) and SMT_LLM_MAX_TOKENS_PER_DAY (default 300,000, India day, from the
   llm_calls table). If the estimate would pass a cap, LLMBudgetExceeded is raised. Never silent.
5. After the call: input and output tokens from the API's usage field are recorded in llm_calls.

Model per task: SMT_CLAUDE_MODEL_<TASK> (task upper-cased, other characters as "_") overrides the
default SMT_CLAUDE_MODEL for that task only.
"""

import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

from smt.store import FeatureStore

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TOKENS_PER_RUN = 100_000
DEFAULT_MAX_TOKENS_PER_DAY = 300_000
IST = timezone(timedelta(hours=5, minutes=30))


class LLMClient(Protocol):
    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str: ...


class LLMBudgetExceeded(RuntimeError):
    """A call would pass a token cap. Nothing was sent. The message shows tokens used so far."""


def estimate_tokens(prompt: str, system: str | None, max_tokens: int) -> tuple[int, int]:
    """(estimated input, most output). About 4 characters a token in English; 3 is used to stay on the safe side."""
    return math.ceil(len((system or "") + prompt) / 3) + 10, max_tokens


def task_env_key(task: str) -> str:
    return "SMT_CLAUDE_MODEL_" + re.sub(r"[^A-Z0-9]", "_", task.upper())


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw.replace("_", ""))
    except ValueError:
        raise ValueError(f"{name} must be a whole number of tokens, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} cannot be negative, got {value}")
    return value


def _today_start_utc(now: datetime | None = None) -> datetime:
    """Start of today in India, as naive UTC (how called_at is stored)."""
    ist = (now or datetime.now(timezone.utc)).astimezone(IST)
    start = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


class GuardedClient:
    """Cache, dry run, caps and token recording. Subclasses only implement _send()."""

    model = "unknown"

    def __init__(self, store: FeatureStore, *, max_run_tokens: int | None = None, max_day_tokens: int | None = None,
                 dry_run: bool | None = None, run_id: str | None = None) -> None:
        self._store = store
        self.max_run_tokens = max_run_tokens if max_run_tokens is not None else _env_int(
            "SMT_LLM_MAX_TOKENS_PER_RUN", DEFAULT_MAX_TOKENS_PER_RUN)
        self.max_day_tokens = max_day_tokens if max_day_tokens is not None else _env_int(
            "SMT_LLM_MAX_TOKENS_PER_DAY", DEFAULT_MAX_TOKENS_PER_DAY)
        self.dry_run = dry_run if dry_run is not None else os.environ.get("SMT_LLM_DRY_RUN", "").strip() == "1"
        self.run_id = run_id
        self.run_tokens = 0       # real tokens used by this client
        self.dry_run_tokens = 0   # estimated tokens of calls skipped by dry run
        self.cache_hits = 0
        self.api_calls = 0

    def model_for(self, task: str) -> str:
        return self.model

    def _send(self, *, model: str, prompt: str, system: str | None, max_tokens: int) -> tuple[str, int, int]:
        """Make the real call. Returns (text, input tokens, output tokens)."""
        raise NotImplementedError

    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        model = self.model_for(task)
        cached = self._store.cached_llm_response(model=model, system=system, prompt=prompt)
        if cached is not None:
            self.cache_hits += 1
            self._store.log_llm_call(task=task, model=model, system=system, prompt=prompt, response=cached,
                                     input_tokens=0, output_tokens=0, cache_hit=True, run_id=self.run_id)
            return cached
        est_in, est_out = estimate_tokens(prompt, system, max_tokens)
        est = est_in + est_out
        day_used = self._store.llm_tokens_since(_today_start_utc())
        if self.dry_run:
            self.dry_run_tokens += est
            over = ""
            if self.run_tokens + self.dry_run_tokens > self.max_run_tokens:
                over = f"  WOULD PASS the run cap of {self.max_run_tokens:,}"
            print(f"[dry run] task={task} model={model} estimated tokens={est:,} (input ~{est_in:,} + up to "
                  f"{est_out:,} output); dry run total {self.dry_run_tokens:,}{over}. No API call.")
            return ""
        caps = []
        if self.run_tokens + est > self.max_run_tokens:
            caps.append(f"the run cap SMT_LLM_MAX_TOKENS_PER_RUN={self.max_run_tokens:,}")
        if day_used + est > self.max_day_tokens:
            caps.append(f"the day cap SMT_LLM_MAX_TOKENS_PER_DAY={self.max_day_tokens:,}")
        if caps:
            raise LLMBudgetExceeded(
                f"LLM budget: task '{task}' ({model}) needs up to about {est:,} tokens (input ~{est_in:,} + up to "
                f"{est_out:,} output), which would pass {' and '.join(caps)}. Used so far: {self.run_tokens:,} this run, "
                f"{day_used:,} today (India time). Nothing was sent. Raise the cap in .env only if this is expected.")
        text, used_in, used_out = self._send(model=model, prompt=prompt, system=system, max_tokens=max_tokens)
        self.api_calls += 1
        self.run_tokens += used_in + used_out
        self._store.log_llm_call(task=task, model=model, system=system, prompt=prompt, response=text,
                                 input_tokens=used_in, output_tokens=used_out, cache_hit=False, run_id=self.run_id)
        return text


class ClaudeClient(GuardedClient):
    def __init__(self, store: FeatureStore, model: str | None = None, **guard) -> None:
        super().__init__(store, **guard)
        import anthropic  # imported here so tests do not need a key

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment; never logged
        self.model = model or os.environ.get("SMT_CLAUDE_MODEL", DEFAULT_MODEL)

    def model_for(self, task: str) -> str:
        return os.environ.get(task_env_key(task), "").strip() or self.model

    def _send(self, *, model: str, prompt: str, system: str | None, max_tokens: int) -> tuple[str, int, int]:
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        u = resp.usage
        used_in = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + (
            getattr(u, "cache_read_input_tokens", 0) or 0)
        return text, used_in, u.output_tokens or 0


class FakeLLM(GuardedClient):
    """For tests. Returns canned text, goes through the same guard and still writes to the recorder.

    `sent` counts calls that reached the (fake) API. Reported usage: the input estimate, and
    `output_tokens` (default: the canned text's length / 3, at least 1).
    """

    model = "fake"

    def __init__(self, store: FeatureStore, responses: dict[str, str] | None = None, default: str = "",
                 output_tokens: int | None = None, **guard) -> None:
        super().__init__(store, **guard)
        self._responses = responses or {}
        self._default = default
        self._output_tokens = output_tokens
        self.sent = 0
        self._task = ""

    def complete(self, *, task: str, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        self._task = task
        return super().complete(task=task, prompt=prompt, system=system, max_tokens=max_tokens)

    def _send(self, *, model: str, prompt: str, system: str | None, max_tokens: int) -> tuple[str, int, int]:
        self.sent += 1
        text = self._responses.get(self._task, self._default)
        out = self._output_tokens if self._output_tokens is not None else max(1, len(text) // 3)
        return text, estimate_tokens(prompt, system, max_tokens)[0], min(out, max_tokens)
