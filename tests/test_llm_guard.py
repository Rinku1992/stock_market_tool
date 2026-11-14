from datetime import date, datetime, timezone

import pytest

from smt.llm import ClaudeClient, FakeLLM, LLMBudgetExceeded, _today_start_utc, estimate_tokens, task_env_key
from smt.llm_usage import summary
from smt.store import FeatureStore


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("SMT_LLM_MAX_TOKENS_PER_RUN", "SMT_LLM_MAX_TOKENS_PER_DAY", "SMT_LLM_DRY_RUN"):
        monkeypatch.delenv(k, raising=False)


def test_tokens_are_recorded():
    s = FeatureStore()
    llm = FakeLLM(s, {"extract": "ok"}, output_tokens=7)
    assert llm.complete(task="extract", prompt="p" * 30, max_tokens=50) == "ok"
    (day, task, model, calls, hits, tin, tout), = s.llm_usage()
    assert (task, model, calls, hits, tout) == ("extract", "fake", 1, 0, 7)
    assert tin == estimate_tokens("p" * 30, None, 50)[0] == 20  # 30 / 3 + 10
    assert llm.run_tokens == 27 and s.llm_tokens_since(_today_start_utc()) == 27


def test_cap_stops_a_call_and_says_what_was_used():
    s = FeatureStore()
    llm = FakeLLM(s, default="x", output_tokens=5, max_run_tokens=200, max_day_tokens=10_000)
    llm.complete(task="a", prompt="first", max_tokens=100)  # estimate 12 + 100 fits
    used = llm.run_tokens
    with pytest.raises(LLMBudgetExceeded) as e:
        llm.complete(task="b", prompt="second", max_tokens=180)  # 17 used + 12 + 180 > 200
    msg = str(e.value)
    assert "SMT_LLM_MAX_TOKENS_PER_RUN=200" in msg and f"Used so far: {used:,} this run" in msg and "Nothing was sent" in msg
    assert llm.sent == 1 and s.count_llm_calls() == 1


def test_day_cap_counts_earlier_runs():
    s = FeatureStore()
    FakeLLM(s, default="x", output_tokens=40).complete(task="a", prompt="p", max_tokens=100)  # 11 + 40 today
    second = FakeLLM(s, default="y", max_day_tokens=100)
    with pytest.raises(LLMBudgetExceeded, match="SMT_LLM_MAX_TOKENS_PER_DAY=100.*51 today"):
        second.complete(task="b", prompt="q", max_tokens=50)
    assert second.sent == 0


def test_caps_come_from_env_with_safe_defaults(monkeypatch):
    llm = FakeLLM(FeatureStore())
    assert (llm.max_run_tokens, llm.max_day_tokens) == (100_000, 300_000)
    monkeypatch.setenv("SMT_LLM_MAX_TOKENS_PER_RUN", "500")
    monkeypatch.setenv("SMT_LLM_MAX_TOKENS_PER_DAY", "2_000")
    llm = FakeLLM(FeatureStore())
    assert (llm.max_run_tokens, llm.max_day_tokens) == (500, 2000)
    monkeypatch.setenv("SMT_LLM_MAX_TOKENS_PER_RUN", "lots")
    with pytest.raises(ValueError, match="whole number"):
        FakeLLM(FeatureStore())


def test_cache_hit_makes_no_call_and_is_counted_separately():
    s = FeatureStore()
    llm = FakeLLM(s, {"t": "answer"}, output_tokens=3)
    assert llm.complete(task="t", prompt="same", system="sys") == "answer"
    assert llm.complete(task="t", prompt="same", system="sys") == "answer"
    assert llm.sent == 1 and llm.cache_hits == 1
    _, _, _, calls, hits, _, tout = s.llm_usage()[0]
    assert (calls, hits, tout) == (1, 1, 3)
    # a different system prompt is not a cache hit
    llm.complete(task="t", prompt="same", system=None)
    assert llm.sent == 2


def test_cache_hit_is_free_even_past_the_cap():
    s = FeatureStore()
    FakeLLM(s, {"t": "a"}).complete(task="t", prompt="p", max_tokens=10)
    assert FakeLLM(s, {"t": "b"}, max_run_tokens=0, max_day_tokens=0).complete(task="t", prompt="p") == "a"


def test_dry_run_makes_no_call(monkeypatch, capsys):
    monkeypatch.setenv("SMT_LLM_DRY_RUN", "1")
    s = FeatureStore()
    llm = FakeLLM(s, {"t": "a"}, max_run_tokens=50)
    assert llm.complete(task="t", prompt="p" * 30, max_tokens=40) == ""
    out = capsys.readouterr().out
    assert "[dry run] task=t model=fake estimated tokens=60" in out and "WOULD PASS the run cap of 50" in out
    assert llm.sent == 0 and s.count_llm_calls() == 0


def test_model_per_task_overrides_only_that_task(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setenv("SMT_CLAUDE_MODEL", "base-model")
    monkeypatch.setenv("SMT_CLAUDE_MODEL_DESK_SUMMARY_MARKET_ACTIVITY", "other-model")
    c = ClaudeClient(FeatureStore())
    assert task_env_key("desk_summary:market_activity") == "SMT_CLAUDE_MODEL_DESK_SUMMARY_MARKET_ACTIVITY"
    assert c.model_for("desk_summary:market_activity") == "other-model"
    assert c.model_for("key_check") == "base-model"


def test_claude_client_records_usage_from_the_response(monkeypatch):
    import anthropic

    class Usage:
        input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens = 12, 5, None, 3

    class Block:
        type, text = "text", "hi"

    class Resp:
        content, usage = [Block()], Usage()

    class Messages:
        def create(self, **kw):
            self.kw = kw
            return Resp()

    class Client:
        messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda: Client())
    s = FeatureStore()
    c = ClaudeClient(s, model="m")
    assert c.complete(task="key_check", prompt="hello", max_tokens=20) == "hi"
    assert s.llm_usage()[0][1:] == ("key_check", "m", 1, 0, 15, 5)


def test_usage_summary():
    rows = [(date(2026, 11, 14), "a", "m1", 2, 1, 100, 20), (date(2026, 11, 14), "b", "m1", 1, 0, 10, 5),
            (date(2026, 11, 13), "a", "m2", 1, 0, 1, 1)]
    s = summary(rows)
    assert s["day"]["2026-11-14"] == [3, 1, 110, 25]
    assert s["task"]["a"] == [3, 1, 101, 21]
    assert s["model"]["m1"] == [3, 1, 110, 25]


def test_today_starts_at_midnight_india_time():
    # 14 Nov 2026 02:00 IST is 13 Nov 20:30 UTC; the India day began at 13 Nov 18:30 UTC
    assert _today_start_utc(datetime(2026, 11, 13, 20, 30, tzinfo=timezone.utc)) == datetime(2026, 11, 13, 18, 30)
