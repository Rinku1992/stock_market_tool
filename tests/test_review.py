"""Independent review (code part) on the made-up filings of test_valuation with the inputs of test_cost_of_capital.
By hand: shares 10 crore x close 280 = market value 2,800; 12-month profit 600; P/E 5.0; P/B 1.0; ROE 21.5%;
revenue 4,000; EBIT 840; borrowings 600; cash 400; EV 3,200."""

from datetime import date, timedelta

from test_adjustments import build  # noqa: F401  (fixture)
from test_cost_of_capital import FILINGS, coc  # noqa: F401  (coc is a fixture)
from test_valuation import AS_OF, ISIN, _build

from smt.analysts import DESKS
from smt.graph import run
from smt.report import build_page, to_markdown
from smt.review import audit, consistency, run_review, set_aside_report
from smt.schema import AnalystKind, AnalystReport, Call, DataGrade, Direction, Evidence, Horizon, HorizonView
from smt.scoring import score
from smt.store import FeatureStore

CR = 1e7


def clean_run(tmp_path, **kw):
    ctx = _build(tmp_path, **kw)
    store = FeatureStore()
    state = run("X", AS_OF, store, lake=ctx.lake, instrument_id=ISIN, desks={"equity_research": DESKS["equity_research"]})
    return state, store, ctx.lake


def report(aid, finding="Some fact.", evidence=(), direction=None):
    direction = direction or ("positive" if evidence else "neutral")  # the schema refuses a view with no evidence
    strength = 0.5 if direction != "neutral" else 0.0
    views = [HorizonView(horizon=h, direction=Direction(direction), strength=strength, confidence=0.3 if strength else 0.0)
             for h in Horizon]
    return AnalystReport(analyst_id=aid, unit="equity_research", kind=AnalystKind.CODE, symbol="X", as_of=AS_OF,
                         finding=finding, evidence=list(evidence), views=views)


def test_clean_run_all_pass_no_veto(tmp_path, coc):
    state, _, _ = clean_run(tmp_path)
    rv = state["review"]
    assert [c for c in rv.audit + rv.checks if c.status != "pass"] == []
    names = {c.name for c in rv.checks}
    assert {"market value", "12-month reported profit", "P/E", "P/B", "ROE", "12-month revenue", "EBIT", "borrowings",
            "cash, bank balances and current investments", "EV before leases"} <= names
    assert any(c.name.startswith("consistency: borrowings on 2025-09-30") for c in rv.checks)
    assert any(c.name.startswith("consistency: cash, bank balances and current investments on 2025-09-30")
               for c in rv.checks)
    assert rv.veto == [] and rv.set_aside == {}
    assert not state["verdict"].horizons[0].reasons[0].startswith("review veto")
    assert "review: LLM reviewers not built" in state["verdict"].not_checked
    mv = next(c for c in rv.checks if c.name == "market value")
    assert mv.text == "re-check market value: recomputed Rs 2,800.00 crore, stored Rs 2,800.00 crore (within 0.5%)"


def test_planted_wrong_market_value_fails_and_vetoes(tmp_path, coc):
    state, store, lake = clean_run(tmp_path)
    store.put_feature(symbol=ISIN, name="val_market_cap", value=2900 * CR, known_at=AS_OF, origin="code",
                      source="planted")  # 3.6% off
    rv = run_review(state, lake, store)
    mv = next(c for c in rv.checks if c.name == "market value")
    assert mv.status == "fail" and mv.critical
    assert mv.text == "re-check market value: recomputed Rs 2,800.00 crore, stored Rs 2,900.00 crore (outside 0.5%)"
    assert rv.veto == [mv.text]
    v = score("X", AS_OF, DataGrade.B, "general", rv.desk_reports, ["equity_research"], None, review=rv)
    assert all(h.call == Call.NO_CALL and h.reasons[0] == f"review veto: {mv.text}" for h in v.horizons)
    page = build_page({**state, "review": rv, "verdict": v}, store, "abc")
    md = to_markdown(page)
    checks = md.split("## 3. Checks")[1].split("## 4. Key numbers")[0]
    assert f"review veto: {mv.text}" in checks
    assert checks.index("FAIL: re-check market value") < checks.index("PASS:")


def test_small_difference_within_tolerance_passes(tmp_path, coc):
    state, store, lake = clean_run(tmp_path)
    store.put_feature(symbol=ISIN, name="val_market_cap", value=2810 * CR, known_at=AS_OF, origin="code", source="t")
    assert next(c for c in run_review(state, lake, store).checks if c.name == "market value").status == "pass"  # 0.36%


def test_no_evidence_and_late_evidence_are_set_aside(tmp_path):
    ctx = _build(tmp_path)
    late = Evidence(id="f", source_type="filing", ref=f"financials/{ISIN}/2025-09-30", as_of=AS_OF + timedelta(days=1))
    good = Evidence(id="g", source_type="filing", ref=f"financials/{ISIN}/2025-09-30", as_of=date(2025, 10, 25))
    ghost = Evidence(id="h", source_type="filing", ref=f"financials/{ISIN}/2023-09-30", as_of=date(2023, 10, 25))
    stub = report("dummy", "not built yet")
    # the schema already refuses late evidence when a report is built; the auditor is a second line, for reports
    # that skipped validation (built without it, or read back from storage)
    late_report = AnalystReport.model_construct(**{**report("b", evidence=[good]).__dict__, "evidence": [late]})
    checks, aside = audit(ctx.lake, [report("a", "Revenue Rs 100 crore."), late_report, report("c", evidence=[good]),
                                     report("d", evidence=[ghost]), stub], ISIN, AS_OF)
    assert aside["a"] == "no evidence item"
    assert aside["b"] == f"evidence f is dated {AS_OF + timedelta(days=1)}, after {AS_OF}"
    assert aside["d"].endswith("no such filing known by 2025-11-28")
    assert "c" not in aside and "dummy" not in aside
    r = set_aside_report(late_report, aside["b"])
    assert all(v.direction == Direction.NEUTRAL for v in r.views) and r.could_not_check[-1].startswith("set aside by review")


def test_weighted_desk_with_every_analyst_set_aside_is_a_veto(tmp_path, coc):
    state, store, lake = clean_run(tmp_path)
    rv = run_review(state, lake, store)
    rv.set_aside = {r.analyst_id: "no evidence item" for r in state["reports"]}
    w = {"version": "t", "weights": {"equity_research": {h.value: 1.0 for h in Horizon}},
         "thresholds": {"min_score": 0.1, "min_confidence": 0.3}}
    v = score("X", AS_OF, DataGrade.B, "general", rv.desk_reports, ["equity_research"], None, weights=w, review=rv)
    assert all(h.call == Call.NO_CALL for h in v.horizons)
    assert v.horizons[0].reasons[0] == "review veto: desk equity_research has every analyst set aside (no valid source)"


def test_same_label_different_cash_is_a_fail():
    a = report("financial_trend", "Balance sheet on 2025-09-30: borrowings Rs 600 crore, cash, bank balances and current "
                                  "investments Rs 400 crore.")
    b = report("relative_valuation", "Enterprise value Rs 3,200 crore (market value + borrowings Rs 600 crore - cash, bank "
                                     "balances and current investments Rs 450 crore on 2025-09-30).")
    out = {c.name: c for c in consistency([a, b])}
    cash = out["consistency: cash, bank balances and current investments on 2025-09-30"]
    assert cash.status == "fail" and "financial_trend 400; relative_valuation 450" in cash.text
    assert out["consistency: borrowings on 2025-09-30"].status == "pass"
    c = report("x", "Balance sheet on 2025-09-30: cash and current investments Rs 350 crore.")  # another label: no clash
    assert all(ch.status == "pass" for ch in consistency([a, c]))


def test_lender_has_no_ev_checks(tmp_path, coc):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    state, _, _ = clean_run(tmp_path, extra=lend)
    names = {c.name for c in state["review"].checks}
    assert "market value" in names and "ROE" in names
    assert not names & {"EV before leases", "EBIT", "borrowings", "12-month revenue",
                        "cash, bank balances and current investments"}
    assert all(c.status == "pass" for c in state["review"].checks)


def test_set_aside_analyst_is_marked_on_the_page(tmp_path, coc):
    state, store, lake = clean_run(tmp_path)
    rv = run_review(state, lake, store)
    rv.set_aside = {"earnings_power": "no evidence item"}
    md = to_markdown(build_page({**state, "review": rv}, store, "abc"))
    assert "#### earnings_power (no valid source: no evidence item; views set aside)" in md


def test_financial_trend_uses_the_valuation_cash_and_shows_both_debt_views(build):
    from test_adjustments import GOLD, LEASES, feat
    from smt.analysts.equity_research import financial_trend
    ctx = build(LEASES + GOLD)
    f = financial_trend(ctx).finding
    # cash 200 + bank balances 50 + current investments 150 = 400; net debt 600 - 400 = 200; debt to equity 600 / 3,000
    assert ("Balance sheet on 2025-09-30: borrowings Rs 600 crore, cash, bank balances and current investments Rs 400 "
            "crore. Before leases: net debt Rs 200 crore, debt to equity 0.20.") in f
    # with leases 400 (FY25) and gold on loan 500 (Sep 2025): net debt 1,100; debt to equity 1,500 / 3,000
    assert "Including leases and gold on loan: net debt Rs 1,100 crore, debt to equity 0.50 (lease liabilities" in f
    assert feat(ctx, "fin_net_debt") == 250 * CR  # old feature, old definition (cash + current investments), kept
    assert feat(ctx, "fin_debt_to_equity") == 0.2
    assert feat(ctx, "fin_net_debt_before_leases") == 200 * CR
    assert feat(ctx, "fin_net_debt_incl_leases_gold") == 1100 * CR
    assert feat(ctx, "fin_debt_to_equity_incl_leases_gold") == 0.5
