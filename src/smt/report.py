"""Stock report: run the engine once for a stock and write one readable page (Markdown and HTML).

    python -m smt.report TCS
    python -m smt.report TITAN --as-of 2026-11-23

Code only, no LLM, no tokens. No new maths: every number on the page is a feature stored by an analyst in
this run, or text from a report of this run. Missing figures print "not available" (with the reason when an
analyst gave one), never 0 and never blank. Analysts' labels ("rough", "not verified", "not material",
stale flags) are kept because their text is printed as written. Banned words never appear: the page is
checked before it is written.

Layout (both files): header, verdict, key numbers (the five questions of docs/VALUATION.md), desks and
analysts, not checked, sources, footer.
"""

import argparse
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from smt.router import LENDER, REAL_ESTATE
from smt.schema import Call

IST = timezone(timedelta(hours=5, minutes=30))
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"
NA = "not available"
BANNED = re.compile(r"\bbuy\b|\bsell\b|\btarget|\bfair value\b|\brecommend", re.I)
FOOTER = "Facts and views from code. Not investment advice. Weights version {}."
GROUPS = ["Clean numbers", "Cash", "Returns", "Price", "What the price assumes"]
_DATE = r"(\d{4}-\d{2}-\d{2})"


# ---------------------------------------------------------------- formats (display only, no maths)

def _cr(v):
    return f"Rs {v / 1e7:,.0f} crore"


FMT = {
    "cr": _cr,
    "pct": lambda v: f"{v:.1%}",
    "spct": lambda v: f"{v:+.1%}",
    "x": lambda v: f"{v:.1f}x",
    "r2": lambda v: f"{v:.2f}",
    "pts": lambda v: f"{v * 100:+.1f} points",
}


@dataclass
class Row:
    group: str
    label: str
    value: str
    basis: str = ""
    features: tuple[str, ...] = ()  # one feature, or (low, base, high) for a range
    fmt: str = ""


@dataclass
class Page:
    symbol: str
    as_of: date
    header: list[tuple[str, str]]
    verdict: list[tuple[str, str, list[str]]]  # (horizon, headline, reasons)
    rows: list[Row]
    desks: list[dict]
    not_checked: list[str]
    sources: list[str]
    footer: str
    notes: dict[str, list[str]] = field(default_factory=dict)  # group -> notes copied from analyst text


# ---------------------------------------------------------------- building the page from one run

def _find(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text or "")
    return m.group(1) if m else None


def build_page(state: dict, store, version: str, facts: list[dict] | None = None) -> Page:
    """state: the graph's output for one run; store: the FeatureStore that run wrote to."""
    v = state["verdict"]
    iid, as_of = state.get("instrument_id"), state["as_of"]
    reports = {r.analyst_id: r for r in state.get("reports", [])}
    gaps = [g for r in reports.values() for g in r.could_not_check]
    text = {k: r.finding for k, r in reports.items()}
    st = v.stock_type or "unknown"

    def feat(name):
        got = store.get_feature_asof(iid, name, as_of) if iid else None
        return None if got is None else got["value"]

    def reason(keyword):
        if not keyword:
            return None
        return next((g for g in gaps if keyword in g), None)

    def missing(keyword=None):
        why = reason(keyword)
        return NA + (f" ({why})" if why else "")

    rv, cr, ep = text.get("relative_valuation", ""), text.get("capital_returns", ""), text.get("earnings_power", "")
    q_end = _find(ep, r"12 months to " + _DATE)
    bs_how = _find(cr, r"invested capital[^(]*\((average of [\d-]+ and [\d-]+|closing [\d-]+ only)")
    lease_date = _find(rv + cr, r"lease liabilities Rs [\d,]+ crore as of " + _DATE)
    gold_date = _find(rv, r"gold on loan Rs [\d,]+ crore as of " + _DATE)
    cf_how = _find(ep, r"Cash flow, ([^:]+):")
    px = re.search(r"close Rs ([\d,.]+) on " + _DATE, rv)
    pe_basis = _find(rv, r"P/E [\d.]+x on reported owners' profit Rs [\d,]+ crore \((12 months to [\d-]+)\)")
    pb_date = _find(rv, r"owners' equity Rs [\d,]+ crore on " + _DATE)
    ev_date = _find(rv, r"cash, bank balances and current investments Rs [\d,]+ crore on " + _DATE)
    m12 = f"12 months to {q_end}" if q_end else ""
    lender, realty = st == LENDER, st == REAL_ESTATE
    has_gold = "gold on loan" in (cr + rv).lower() or any("gold on loan" in g for g in gaps)

    rows: list[Row] = []

    def add(group, label, name, fmt, basis="", keyword=None):
        x = feat(name)
        rows.append(Row(group, label, FMT[fmt](x) if x is not None else missing(keyword), basis if x is not None else "",
                        (name,), fmt))

    def add_range(group, label, base, low, high, fmt, basis="", keyword=None):
        b, lo, hi = feat(base), feat(low), feat(high)
        if None in (b, lo, hi):
            rows.append(Row(group, label, missing(keyword), "", (low, base, high), fmt))
        else:
            f = FMT[fmt]
            rows.append(Row(group, label, f"{f(lo)} to {f(hi)} (base {f(b)})", basis, (low, base, high), fmt))

    # 1. clean numbers
    if not lender:
        add("Clean numbers", "Lease liabilities", "val_lease_liabilities", "cr",
            f"as of {lease_date}, from the annual report" if lease_date else "", "leases:")
        if has_gold:
            add("Clean numbers", "Gold on loan", "val_gold_on_loan", "cr", f"as of {gold_date}" if gold_date else "",
                "gold on loan")
        add("Clean numbers", "R&D asset (rough)", "val_rd_asset_rough", "cr", "", "R&D")
        add("Clean numbers", "ROIC with R&D capitalised, R&D only (rough)", "val_roic_rd_rough", "pct", m12, "R&D")
        add("Clean numbers", "ROIC with R&D capitalised, R&D and innovation (rough)", "val_roic_rd_wide_rough", "pct",
            m12, "R&D")
    # 2. cash
    add("Cash", "Reported profit", "val_profit_ttm", "cr", m12, "reported profit")
    add("Cash", "Core profit (before exceptional items)", "val_core_profit_ttm", "cr", m12, "core profit")
    if not lender:
        add("Cash", "Free cash flow", "val_fcf_ttm", "cr", cf_how or "", "cash flow")
        add("Cash", "Cash conversion (operating cash / profit)", "val_cash_conversion", "r2", cf_how or "", "cash flow")
        add("Cash", "Accruals (share of total assets)", "val_accruals", "spct", cf_how or "", "cash flow")
    # 3. returns
    if lender:
        add("Returns", "ROE", "val_roe", "pct", m12, "ROE")
        add("Returns", "Return on assets", "val_roa", "pct", m12)
        add("Returns", "Credit cost", "val_credit_cost_ttm", "cr", m12, "credit cost")
        add_range("Returns", "Cost of equity", "val_cost_of_equity", "val_cost_of_equity_low", "val_cost_of_equity_high",
                  "pct", "", "cost of capital")
        add_range("Returns", "ROE minus cost of equity", "val_roe_spread", "val_roe_spread_low", "val_roe_spread_high",
                  "pts", "", "ROE")
    else:
        if has_gold:
            add("Returns", "ROIC including leases and gold on loan", "val_roic_incl_leases_gold", "pct",
                f"{m12}; {bs_how}" if bs_how else m12, "gold on loan")
        add("Returns", "ROIC including leases", "val_roic_incl_leases", "pct", f"{m12}; {bs_how}" if bs_how else m12,
            "leases:")
        add("Returns", "ROIC before leases", "val_roic", "pct", f"{m12}; {bs_how}" if bs_how else m12, "invested capital")
        if realty:
            add("Returns", "EBIT excluding other income", "val_ebit_ex_oi_ttm", "cr", m12)
        add("Returns", "ROE", "val_roe", "pct", m12)
        add_range("Returns", "Cost of equity", "val_cost_of_equity", "val_cost_of_equity_low", "val_cost_of_equity_high",
                  "pct", "", "cost of capital")
        add("Returns", "Cost of debt (before tax)", "val_cost_of_debt", "pct", m12, "cost of debt")
        add_range("Returns", "WACC", "val_wacc", "val_wacc_low", "val_wacc_high", "pct", "",
                  "cost of debt")
        add_range("Returns", "Lead ROIC minus WACC", "val_roic_spread", "val_roic_spread_low", "val_roic_spread_high",
                  "pts", "", "cost of debt")
    # 4. price
    add("Price", "Market value", "val_market_cap", "cr", f"close on {px.group(2)}" if px else "", "market value")
    if not lender:
        for name, view in (("val_ev_incl_leases_gold", "including leases and gold on loan"),
                           ("val_ev_incl_leases", "including leases"), ("val_ev", "before leases")):
            if feat(name) is not None:
                add("Price", f"Enterprise value ({view}, lead)", name, "cr", f"on {ev_date}" if ev_date else "")
                break
        else:
            rows.append(Row("Price", "Enterprise value", missing("EV"), "", ("val_ev",)))
    add("Price", "P/E", "val_pe", "x", pe_basis or "", "owners' profit")
    add("Price", "P/B", "val_pb", "x", f"owners' equity on {pb_date}" if pb_date else "", "owners' equity")
    if not lender:
        for name, view in (("val_ev_ebitda_incl_leases_gold", "including leases and gold on loan"),
                           ("val_ev_ebitda_incl_leases", "including leases"), ("val_ev_ebitda", "before leases")):
            if feat(name) is not None:
                add("Price", f"EV/EBITDA ({view}, lead)", name, "x", m12)
                break
        else:
            rows.append(Row("Price", "EV/EBITDA", missing("EBITDA"), "", ("val_ev_ebitda",)))
    add("Price", "Dividend yield", "val_dividend_yield", "pct", "", "dividend yield")
    if not lender:
        add("Price", "FCF yield", "val_fcf_yield", "pct", cf_how or "", "cash flow")
    # 5. what the price assumes
    if lender:
        add_range("What the price assumes", "Lasting ROE the price assumes (growth = risk-free)", "val_implied_roe",
                  "val_implied_roe_low", "val_implied_roe_high", "pct", "cost of equity low to high", "implied ROE")
    else:
        add("What the price assumes", "Share of EV explained by today's profit with no growth", "val_no_growth_share",
            "pct", "base WACC", "implied growth")
        add_range("What the price assumes", "Growth a year the price assumes, 10 years (lead ROIC)",
                  "val_implied_growth_10y", "val_implied_growth_10y_low", "val_implied_growth_10y_high", "pct",
                  "WACC low to high", "implied growth")
        add("What the price assumes", "Growth a year the price assumes, 15 years (lead ROIC)", "val_implied_growth_15y",
            "pct", "base WACC", "implied growth")

    notes = {}
    rd_line = next((s for s in re.split(r"(?<=\.) (?=[A-Z])", cr) if "R&D" in s and "not material" in s), None)
    if rd_line:
        notes["Clean numbers"] = [rd_line]
    if realty:
        notes["What the price assumes"] = ["Real estate: profit is lumpy (booked on handover), so this result is not "
                                           "reliable."]
    rvt = text.get("reverse_valuation", "")
    if "no growth rate up to 60%" in rvt:
        notes.setdefault("What the price assumes", []).append(
            "In some cases no growth rate up to 60% a year explains the price under these assumptions (see the reverse "
            "valuation text).")

    header = [("Symbol", state["symbol"]), ("Stock type", st), ("As of", str(as_of)),
              ("Latest price", f"Rs {px.group(1)} on {px.group(2)}" if px else missing("price")),
              ("Data grade", v.data_grade.value), ("Engine commit", version)]
    verdict = []
    for h in v.horizons:
        head = (f"No call (confidence {h.confidence:.2f})." if h.call == Call.NO_CALL
                else f"Call: {h.call.value}, confidence {h.confidence:.2f}.")
        verdict.append((h.horizon.value, head, list(h.reasons)))
    if v.refused_reason:
        verdict = [("all", f"No call. Refused: {v.refused_reason}", [])]

    from smt.analysts import DESKS

    order = {u: i for i, u in enumerate(DESKS)}
    desks = []
    for d in sorted(state.get("desk_reports", []), key=lambda d: order.get(d.unit, len(order))):
        desks.append({"unit": d.unit, "views": d.views, "notes": d.notes, "conflicts": d.conflicts,
                      "analysts": [r for r in state.get("reports", []) if r.unit == d.unit]})

    sources, seen = [], set()
    for r in state.get("reports", []):
        for e in r.evidence:
            if e.id not in seen:
                seen.add(e.id)
                sources.append(f"{e.source_type}: {e.ref} (as of {e.as_of})")
    for f in facts or []:
        sources.append(f"annual report fact: {f['item']} {_cr(f['value'])} for {f['fiscal_year_end']}, page {f['page']}, "
                       f"published {f['published_at']:%Y-%m-%d}" + (f", {f['extractor']}" if f.get("extractor") else ""))
    return Page(state["symbol"], as_of, header, verdict, rows, desks, list(v.not_checked) or ["nothing listed"],
                sources or ["none"], FOOTER.format(v.weights_version or "none"), notes)


# ---------------------------------------------------------------- rendering

def _views(views) -> str:
    return "; ".join(f"{x.horizon.value} {x.direction.value}" + (f" (strength {x.strength:.2f}, confidence "
                                                                   f"{x.confidence:.2f})" if x.direction.value != "neutral"
                                                                   else "") for x in views)


def to_markdown(p: Page) -> str:
    out = [f"# {p.symbol}: stock report, {p.as_of}", "", "## 1. Header", ""]
    out += [f"- **{k}:** {v}" for k, v in p.header]
    out += ["", "## 2. Verdict", ""]
    for h, head, reasons in p.verdict:
        out.append(f"**{h}:** {head}")
        out += [f"- {r}" for r in reasons]
        out.append("")
    out += ["## 3. Key numbers", ""]
    for g in GROUPS:
        rows = [r for r in p.rows if r.group == g]
        out += [f"### {g}", ""]
        if rows:
            out += ["| Figure | Value | Basis |", "|---|---|---|"]
            out += [f"| {r.label} | {r.value} | {r.basis or '-'} |" for r in rows]
        else:
            out.append("None for this stock type.")
        out += [f"\n_{n}_" for n in p.notes.get(g, [])]
        out.append("")
    out += ["## 4. Desks and analysts", ""]
    for d in p.desks:
        out += [f"### Desk: {d['unit']}", "", f"Desk views: {_views(d['views'])}", ""] + [f"- {n}" for n in d["notes"]]
        out.append("")
        for c in d["conflicts"]:
            out.append(f"- Conflict ({c.horizon.value}): positive {', '.join(c.positive)}; negative {', '.join(c.negative)}")
        for a in d["analysts"]:
            out += [f"#### {a.analyst_id}", "", a.finding, "", f"Views: {_views(a.views)}", ""]
    out += ["## 5. Not checked", ""] + [f"- {x}" for x in p.not_checked]
    out += ["", "## 6. Sources", ""] + [f"- {x}" for x in p.sources]
    out += ["", "---", "", p.footer, ""]
    return "\n".join(out)


CSS = """body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:960px;margin:0 auto;padding:16px;
color:#1d1d1f;background:#fff;line-height:1.5}h1{font-size:1.6em}h2{border-bottom:1px solid #ddd;padding-bottom:4px;
margin-top:1.6em}table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #ddd;padding:6px 8px;
text-align:left;vertical-align:top}th{background:#f4f4f6}.na{color:#9a4b00}.call{font-weight:600}.muted{color:#666}
p.finding{white-space:pre-wrap}@media (prefers-color-scheme:dark){body{background:#151517;color:#e8e8ea}
th{background:#26262a}td,th{border-color:#3a3a40}h2{border-color:#3a3a40}.muted{color:#aaa}}"""


def to_html(p: Page) -> str:
    e = html.escape
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           f"<title>{e(p.symbol)} stock report {p.as_of}</title><style>{CSS}</style></head><body>",
           f"<h1>{e(p.symbol)}: stock report, {p.as_of}</h1>", "<h2>1. Header</h2><table>"]
    out += [f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in p.header]
    out += ["</table>", "<h2>2. Verdict</h2>"]
    for h, head, reasons in p.verdict:
        out.append(f"<p class='call'>{e(h)}: {e(head)}</p><ul>" + "".join(f"<li>{e(r)}</li>" for r in reasons) + "</ul>")
    out.append("<h2>3. Key numbers</h2>")
    for g in GROUPS:
        rows = [r for r in p.rows if r.group == g]
        out.append(f"<h3>{e(g)}</h3>")
        if rows:
            out.append("<table><tr><th>Figure</th><th>Value</th><th>Basis</th></tr>")
            for r in rows:
                cls = " class='na'" if r.value.startswith(NA) else ""
                out.append(f"<tr><td>{e(r.label)}</td><td{cls}>{e(r.value)}</td><td>{e(r.basis or '-')}</td></tr>")
            out.append("</table>")
        else:
            out.append("<p class='muted'>None for this stock type.</p>")
        out += [f"<p class='muted'><em>{e(n)}</em></p>" for n in p.notes.get(g, [])]
    out.append("<h2>4. Desks and analysts</h2>")
    for d in p.desks:
        out += [f"<h3>Desk: {e(d['unit'])}</h3>", f"<p>Desk views: {e(_views(d['views']))}</p>"]
        out += [f"<p class='muted'>{e(n)}</p>" for n in d["notes"]]
        for c in d["conflicts"]:
            out.append(f"<p>Conflict ({e(c.horizon.value)}): positive {e(', '.join(c.positive))}; negative "
                       f"{e(', '.join(c.negative))}</p>")
        for a in d["analysts"]:
            out += [f"<h4>{e(a.analyst_id)}</h4>", f"<p class='finding'>{e(a.finding)}</p>",
                    f"<p class='muted'>Views: {e(_views(a.views))}</p>"]
    out += ["<h2>5. Not checked</h2><ul>"] + [f"<li>{e(x)}</li>" for x in p.not_checked] + ["</ul>"]
    out += ["<h2>6. Sources</h2><ul>"] + [f"<li>{e(x)}</li>" for x in p.sources] + ["</ul>"]
    out += [f"<hr><p class='muted'>{e(p.footer)}</p></body></html>"]
    return "\n".join(out)


def check_words(text: str) -> None:
    m = BANNED.search(text)
    if m:
        raise ValueError(f"banned word on the page: {m.group(0)!r} near {text[max(0, m.start() - 60):m.end() + 60]!r}")


def write(p: Page, folder: Path = REPORTS_DIR) -> tuple[Path, Path, str]:
    md, ht = to_markdown(p), to_html(p)
    check_words(md)
    check_words(ht)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{p.symbol}_{p.as_of}"
    pm, ph = folder / f"{stem}.md", folder / f"{stem}.html"
    pm.write_text(md)
    ph.write_text(ht)
    return pm, ph, md


# ---------------------------------------------------------------- command

def make_report(symbol: str, as_of: date, lake_path: str = "data/lake", folder: Path = REPORTS_DIR):
    from smt.analysts import adjustments as adj
    from smt.calls import code_version
    from smt.data.lake import Lake
    from smt.data.members import symbol_to_isin
    from smt.graph import run
    from smt.store import FeatureStore

    lake = Lake(lake_path)
    iid = symbol_to_isin(lake).get(symbol.upper())
    if iid is None:
        raise SystemExit(f"{symbol}: not in the loaded member lists, so no instrument id")
    store = FeatureStore()
    state = run(symbol, as_of, store, lake=lake, instrument_id=iid)
    facts = [r for r in adj.facts_asof(lake, iid, as_of).values() if adj.usable(r)]
    facts.sort(key=lambda r: (r["item"], r["fiscal_year_end"]))
    return write(build_page(state, store, code_version(), facts), folder)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="smt.report", description="Run the engine for one stock and write a report page.")
    p.add_argument("symbol")
    p.add_argument("--as-of", type=date.fromisoformat, default=None, help="default: today (India time)")
    a = p.parse_args(argv)
    as_of = a.as_of or datetime.now(IST).date()
    pm, ph, md = make_report(a.symbol, as_of)
    print(md)
    print(f"Wrote {pm} and {ph}")


if __name__ == "__main__":
    main()
