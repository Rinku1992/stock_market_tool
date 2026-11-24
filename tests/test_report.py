"""Stock report on the made-up filings of test_valuation, with the cost of capital inputs of
test_cost_of_capital (beta 1.0). No new maths: every number must equal a stored feature."""

import pytest
from test_cost_of_capital import FILINGS, coc  # noqa: F401  (coc is a fixture)
from test_valuation import AS_OF, ISIN, _build

from smt.graph import run
from smt.report import BANNED, FMT, FOOTER, NA, build_page, check_words, to_html, to_markdown, write
from smt.store import FeatureStore

SECTIONS = ["## 1. Header", "## 2. Verdict", "## 3. Checks", "## 4. Key numbers", "## 5. Desks and analysts",
            "## 6. Not checked", "## 7. Sources"]


def page_for(tmp_path, **kw):
    ctx = _build(tmp_path, **kw)
    store = FeatureStore()
    state = run("X", AS_OF, store, lake=ctx.lake, instrument_id=ISIN)
    return build_page(state, store, "abc1234"), store


def values(store, names):
    out = []
    for n in names:
        got = store.get_feature_asof(ISIN, n, AS_OF)
        out.append(None if got is None else got["value"])
    return out


def test_both_files_have_all_seven_sections(tmp_path, coc):
    p, _ = page_for(tmp_path)
    pm, ph, md = write(p, tmp_path / "reports")
    assert pm.name == f"X_{AS_OF}.md" and ph.name == f"X_{AS_OF}.html" and md == pm.read_text()
    for s in SECTIONS:
        assert s in md
    assert FOOTER.format(p.footer.split("version ")[1].rstrip(".")) in md
    ht = ph.read_text()
    for s in SECTIONS:
        assert f"<h2>{s[3:]}</h2>" in ht
    assert "Facts and views from code. Not investment advice." in ht
    assert "<script" not in ht and "http" not in ht  # self-contained: no outside scripts or fonts
    order = [md.index(s) for s in SECTIONS] + [md.index("Facts and views from code")]
    assert order == sorted(order)


def test_every_key_number_equals_its_stored_feature(tmp_path, coc):
    p, store = page_for(tmp_path)
    checked = 0
    for r in p.rows:
        vals = values(store, r.features)
        if r.value.startswith(NA):
            assert None in vals, r.label
            continue
        f = FMT[r.fmt]
        if len(vals) == 1:
            assert r.value == f(vals[0]), r.label
        else:
            lo, base, hi = vals
            assert r.value == f"{f(lo)} to {f(hi)} (base {f(base)})", r.label
        checked += 1
    assert checked >= 15
    roic = next(r for r in p.rows if r.label == "ROIC before leases")
    assert roic.value == "21.0%" and "average of 2024-09-30 and 2025-09-30" in roic.basis


def test_missing_feature_prints_not_available_with_reason(tmp_path, coc):
    p, _ = page_for(tmp_path)
    lease = next(r for r in p.rows if r.label == "Lease liabilities")
    assert lease.value.startswith("not available (leases: no lease liabilities from an annual report")
    assert all(r.value for r in p.rows) and not any(r.value in ("0", "Rs 0 crore") for r in p.rows if NA in r.value)
    md = to_markdown(p)
    assert "| Lease liabilities | not available (leases:" in md
    assert "Gold on loan" not in md  # no gold rows for a stock with no gold on loan facts


def test_lender_page_has_no_ev_or_roic_rows(tmp_path, coc):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    p, store = page_for(tmp_path, extra=lend)
    labels = [r.label for r in p.rows]
    assert not any("ROIC" in x or "Enterprise value" in x or "EV/" in x or "FCF" in x or "WACC" in x
                   or "Free cash flow" in x or "Lease" in x for x in labels)
    assert "ROE" in labels and "Return on assets" in labels and "P/B" in labels
    roe = next(r for r in p.rows if r.label.startswith("Lasting ROE"))
    lo, base, hi = values(store, roe.features)
    assert roe.value == f"{lo:.1%} to {hi:.1%} (base {base:.1%})"


def test_verdict_says_no_call_first(tmp_path, coc):
    p, _ = page_for(tmp_path)
    assert all(head.startswith("No call") for _, head, _ in p.verdict)
    assert any("every verdict weight is 0" in r for _, _, rs in p.verdict for r in rs)


def test_banned_words_never_appear(tmp_path, coc):
    p, _ = page_for(tmp_path)
    for text in (to_markdown(p), to_html(p)):
        assert not BANNED.search(text)
    with pytest.raises(ValueError, match="banned word"):
        check_words("a good time to buy")
    check_words("buyback and selling expenses")  # whole words only
