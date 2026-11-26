"""Portfolio and position desk on made-up holdings and prices only (never real holdings).

Two-stock maths by hand: weights 0.6 / 0.4, daily volatilities 2% / 1%, correlation 0.5:
covariance matrix [[4e-4, 1e-4], [1e-4, 1e-4]]; portfolio variance 0.36 x 4e-4 + 2 x 0.24 x 1e-4 + 0.16 x 1e-4 = 2.08e-4;
risk share A = 0.6 x (0.6 x 4e-4 + 0.4 x 1e-4) / 2.08e-4 = 0.8077; B = 0.4 x (0.6e-4 + 0.4e-4) / 2.08e-4 = 0.1923.
"""

import json
import math
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from smt.analysts import portfolio as pf
from smt.analysts.base import AnalystContext
from smt.calls import call_rows
from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.graph import run
from smt.report import BANNED, build_portfolio_page, portfolio_html, portfolio_markdown
from smt.store import FeatureStore
from smt.thesis import ThesisEntry, entries

UTC = timezone.utc
D = date(2026, 6, 30)
A_, B_, C_ = "INE000000A01", "INE000000B01", "INE000000C01"


class Holdings:
    name, dataset, raw_ext, context = "test_holdings", "holdings", "json", {}

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return json.dumps(self.rows, default=str).encode()

    def parse(self, payload):
        return [{**r, "as_of": date.fromisoformat(r["as_of"]), "published_at": datetime.fromisoformat(r["published_at"])}
                for r in json.loads(payload)]


def _prices(lake, iid, closes, end=D):
    rows = []
    for k, c in enumerate(closes):
        d = end - timedelta(days=len(closes) - 1 - k)
        rows.append({"instrument_id": iid, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted", "open": c,
                     "high": c, "low": c, "close": c, "volume": 1000, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2026, 7, 1, tzinfo=UTC), "raw_sha256": "p", "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows), iid)


def _series(n, step, seed):
    """Deterministic wiggly prices."""
    out, p = [], 100.0
    for k in range(n):
        p *= 1 + step * math.sin(k * seed + seed)
        out.append(p)
    return out


def make_lake(tmp_path, days=260, with_c=True, industry=True):
    lake = Lake(tmp_path / "lake")
    rows = [{"account": "main", "instrument_id": A_, "as_of": "2026-06-29", "quantity": 100, "avg_price": 80.0,
             "source_ref": "AAA", "published_at": "2026-06-29T12:00:00+00:00"},
            {"account": "main", "instrument_id": B_, "as_of": "2026-06-29", "quantity": 50, "avg_price": 100.0,
             "source_ref": "BBB", "published_at": "2026-06-29T12:00:00+00:00"}]
    if with_c:
        rows.append({"account": "main", "instrument_id": C_, "as_of": "2026-06-29", "quantity": 10, "avg_price": 50.0,
                     "source_ref": "CCC", "published_at": "2026-06-29T12:00:00+00:00"})
    ingest(Holdings(rows), lake)
    _prices(lake, A_, _series(days, 0.02, 0.7)[:-1] + [120.0])
    _prices(lake, B_, _series(days, 0.01, 1.3)[:-1] + [80.0])
    _prices(lake, "IDX:NIFTY50", _series(days, 0.01, 0.9))
    if industry:
        lake.write("index_members", pa.Table.from_pylist([
            {"index_id": "IDX:NIFTY100", "instrument_id": A_, "valid_from": date(2026, 1, 1), "valid_to": None,
             "industry": "Consumer", "source": "t", "source_ref": None, "published_at": datetime(2026, 1, 1, tzinfo=UTC),
             "ingested_at": datetime(2026, 1, 1, tzinfo=UTC), "raw_sha256": "m", "schema_version": 2}]), "m")
    return lake


def ctx(lake, iid, sym):
    return AnalystContext(symbol=sym, as_of=D, store=FeatureStore(), lake=lake, instrument_id=iid)


def feat(c, name):
    got = c.store.get_feature_asof(c.instrument_id, name, D)
    return None if got is None else got["value"]


# ---------------------------------------------------------------- pure maths

def test_two_stock_risk_shares_by_hand():
    S, w = [[4e-4, 1e-4], [1e-4, 1e-4]], [0.6, 0.4]
    assert pf.port_variance(S, w) == pytest.approx(2.08e-4)
    shares = pf.risk_shares(S, w)
    assert shares == pytest.approx([0.6 * 2.8 / 2.08, 0.4 * 1.0 / 2.08]) and sum(shares) == pytest.approx(1.0)
    assert pf.annual_vol(pf.port_variance(S, w)) == pytest.approx(math.sqrt(2.08e-4 * 252))
    assert pf.cov([1, 2, 3], [2, 4, 6]) == pytest.approx(2.0) and pf.corr([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


@pytest.mark.parametrize("limit", [0.10, 0.30, 0.50])
def test_limits_just_inside_and_just_above(limit):
    assert pf.limit_text(limit, limit) == "inside the limit"
    assert pf.limit_text(limit - 0.0001, limit) == "inside the limit"
    assert pf.limit_text(limit + 0.0001, limit) == f"above the {limit:.0%} limit"


def test_shipped_limits_are_owner_settings():
    st = pf.load_settings()
    assert st["limits"] == {"single_stock": 0.10, "single_sector": 0.30, "top_five": 0.50}
    assert st["label"] == "owner settings, starting values" and st["account_alias"] == "main"


# ---------------------------------------------------------------- analysts on a made-up lake

def test_position_size_holding_context_and_not_covered(tmp_path):
    lake = make_lake(tmp_path)
    c = ctx(lake, A_, "AAA")
    f = pf.position_size(c).finding
    # market values: A 100 x 120 = 12,000; B 50 x 80 = 4,000; C has no price -> weights 75% / 25%
    assert "3 holdings, 2 covered by stored prices." in f
    assert "AAA: weight 75.0% (rank 1 of 2); single stock limit 10% (owner settings, starting values): above the 10% limit." in f
    assert "Sector Consumer: 75.0%; single sector limit 30%: above the 30% limit." in f
    assert "Sector weights: Consumer 75.0%, sector not known 25.0%." in f
    assert "Limit breached: AAA 75.0% above the single stock limit 10%; BBB 25.0% above the single stock limit 10%" in f
    assert "Not covered (no stored price, left out of the maths): CCC." in f
    assert "trim" not in f.lower()
    assert feat(c, "pf_weight") == pytest.approx(0.75)
    h = pf.holding_context(c).finding
    assert "money invested Rs 8,000" in h and "Market value Rs 12,000" in h and "gain Rs 4,000 (+50.0%)" in h
    assert "not available: stored holdings carry no buy dates (needs trade history)" in h
    b = ctx(lake, B_, "BBB")
    pf.position_size(b)
    assert "Sector sector not known: 25.0%; no sector limit check." in pf.position_size(b).finding


def test_overlap_risk_shares_add_up(tmp_path):
    lake = make_lake(tmp_path)
    a, b = ctx(lake, A_, "AAA"), ctx(lake, B_, "BBB")
    fa, _ = pf.overlap(a).finding, pf.overlap(b)
    assert "Daily returns over the last 250 trading days common to all 2 covered holdings" in fa
    assert "portfolio beta to the Nifty 50" in fa and "Most correlated holdings: BBB" in fa
    assert feat(a, "pf_risk_share") + feat(b, "pf_risk_share") == pytest.approx(1.0)
    book = pf.get_book(lake, D)
    S = pf.cov_matrix(book.risk["rets"])
    assert book.risk["vol"] == pytest.approx(math.sqrt(pf.port_variance(S, book.weights) * 252))


def test_fewer_than_120_common_days(tmp_path):
    lake = make_lake(tmp_path, days=100)
    f = pf.overlap(ctx(lake, A_, "AAA")).finding
    assert f == "Overlap not computed: only 99 daily returns on dates common to all covered holdings (needs 120)."


def test_stock_not_held_reports_not_held_and_adds_no_call_rows(tmp_path):
    lake = make_lake(tmp_path)
    c = ctx(lake, "INE999999Z01", "ZZZ")
    for a in (pf.position_size, pf.overlap, pf.holding_context, pf.thesis_tracker):
        assert a(c).finding == "not held"
    state = run("ZZZ", D, FeatureStore(), lake=lake, instrument_id="INE999999Z01",
                desks={"portfolio": [pf.position_size, pf.holding_context]}, grade_fn=lambda x: pf_grade_b())
    rows = call_rows(state, "INE999999Z01", 1.0, "abc", datetime(2026, 6, 30, 11, tzinfo=UTC))
    assert {r["source_id"] for r in rows} == {"verdict"}
    held = run("AAA", D, FeatureStore(), lake=lake, instrument_id=A_, desks={"portfolio": [pf.position_size]})
    rows = call_rows(held, A_, 120.0, "abc", datetime(2026, 6, 30, 11, tzinfo=UTC))
    assert {r["source_id"] for r in rows} == {"position_size", "portfolio", "verdict"}


def pf_grade_b():
    from smt.schema import DataGrade
    return DataGrade.B


def test_thesis_set_twice_keeps_both_and_shows_latest(tmp_path):
    lake = make_lake(tmp_path)
    ingest(ThesisEntry(A_, "AAA", "first reason", datetime(2026, 6, 1, 9, tzinfo=UTC)), lake)
    ingest(ThesisEntry(A_, "AAA", "second reason", datetime(2026, 6, 20, 9, tzinfo=UTC)), lake)
    got = entries(lake, A_)
    assert [t for t, _ in got] == ["second reason", "first reason"]
    f = pf.thesis_tracker(ctx(lake, A_, "AAA")).finding
    assert f.startswith('Thesis for AAA, recorded 2026-06-20: "second reason"') and "needs the LLM" in f
    assert pf.thesis_tracker(ctx(lake, B_, "BBB")).finding.startswith("No thesis recorded for BBB.")


def test_portfolio_page_has_every_section_and_no_banned_words(tmp_path):
    lake = make_lake(tmp_path)
    book = pf.get_book(lake, D)
    runs = []
    for h in book.covered + book.not_covered:
        store = FeatureStore()
        runs.append((h, run(h.symbol, D, store, lake=lake, instrument_id=h.iid,
                            desks={"portfolio": [pf.position_size, pf.overlap, pf.holding_context]}), store))
    p = build_portfolio_page(book, runs, "abc")
    md, ht = portfolio_markdown(p), portfolio_html(p)
    for i, s in enumerate(["Header", "Holdings", "Sector weights", "Risk", "Limits", "Not covered"], 1):
        assert f"## {i}. {s}" in md and f"<h2>{i}. {s}</h2>" in ht
    assert "Facts and views from code. Not investment advice." in md
    assert "| AAA | 75.0% |" in md and "| CCC | not available | not available |" in md
    assert "Not covered (no stored price, left out of the maths): CCC." in md
    assert "Limit breached:" in md and "| Consumer | 75.0% |" in md
    assert not BANNED.search(md) and not BANNED.search(ht)
