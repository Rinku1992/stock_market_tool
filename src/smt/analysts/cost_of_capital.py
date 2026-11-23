"""Cost of capital (valuation stage C step 1). Pure functions, code only, no LLM.

    risk-free     = 10-year G-sec yield - India default spread
                    (the bond yield already holds India's default risk, and the country risk premium holds
                    it too: it is taken off once so it is not counted twice)
    beta (Blume)  = 0.67 x raw 1-year beta + 0.33, kept inside 0.6 to 1.5; low = base - 0.2, high = base + 0.2
    cost of equity = risk-free + beta x (mature market ERP + India country risk premium)
    cost of debt  = 12-month finance costs / average debt (before tax); used only inside 4% to 15%
    WACC          = E/(E+D) x cost of equity + D/(E+D) x cost of debt x (1 - tax rate), market value weights
    spread        = ROIC - WACC (lenders: ROE - cost of equity)

All rates are fractions (0.065 = 6.5%).
"""

from dataclasses import dataclass

BLUME_SLOPE, BLUME_CONST = 0.67, 0.33
BETA_MIN, BETA_MAX = 0.6, 1.5
BETA_BAND = 0.2
KD_MIN, KD_MAX = 0.04, 0.15
BETA_LABEL = "rough: one year of prices; an industry beta needs peers (later)"
RF_NOTE = ("the bond yield already holds India's default risk and the country risk premium holds it too, so the "
           "default spread is taken off once, not counted twice")
GROWTH_LINE = "Growth adds value only when ROIC is above the cost of capital."


@dataclass(frozen=True)
class Beta:
    raw: float
    base: float
    low: float
    high: float
    clamped: bool


def risk_free(gsec: float, default_spread: float) -> float:
    return gsec - default_spread


def blume_beta(raw: float) -> Beta:
    adj = BLUME_SLOPE * raw + BLUME_CONST
    base = min(BETA_MAX, max(BETA_MIN, adj))
    return Beta(raw, base, base - BETA_BAND, base + BETA_BAND, base != adj)


def cost_of_equity(rf: float, beta: float, mature_erp: float, crp: float) -> float:
    return rf + beta * (mature_erp + crp)


def cost_of_equity_range(rf: float, b: Beta, mature_erp: float, crp: float) -> tuple[float, float, float]:
    """(low, base, high)."""
    return tuple(cost_of_equity(rf, x, mature_erp, crp) for x in (b.low, b.base, b.high))


def cost_of_debt(finance_costs: float | None, avg_debt: float | None) -> tuple[float | None, str | None]:
    """Before tax, or None and the reason."""
    if finance_costs is None:
        return None, "12-month finance costs are not loaded"
    if avg_debt is None:
        return None, "debt is missing"
    if avg_debt <= 0:
        return None, "debt is zero"
    kd = finance_costs / avg_debt
    if not KD_MIN <= kd <= KD_MAX:
        return None, f"finance costs / average debt is {kd:.1%}, outside {KD_MIN:.0%} to {KD_MAX:.0%}"
    return kd, None


def wacc(equity_value: float, debt: float, ke: float, kd: float, tax_rate: float) -> float:
    total = equity_value + debt
    return equity_value / total * ke + debt / total * kd * (1 - tax_rate)


def wacc_range(equity_value: float, debt: float, ke: tuple[float, float, float], kd: float,
               tax_rate: float) -> tuple[float, float, float]:
    return tuple(wacc(equity_value, debt, k, kd, tax_rate) for k in ke)


def spread_range(ret: float, low: float, high: float) -> tuple[float, float]:
    """(spread against the high cost, spread against the low cost)."""
    return ret - high, ret - low
