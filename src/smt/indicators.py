"""Pure maths on price series. No I/O. Every function returns None when there is too little data."""

import math
from datetime import date
from statistics import median

DAYS_3M, DAYS_6M, DAYS_1Y = 63, 126, 250  # trading days


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def pct_change(xs: list[float], n: int) -> float | None:
    return xs[-1] / xs[-1 - n] - 1 if len(xs) > n else None


def dist_from_high(xs: list[float], n: int = DAYS_1Y) -> float | None:
    """0.0 at the high, -0.25 means 25% below the highest close of the last n days."""
    return xs[-1] / max(xs[-n:]) - 1 if len(xs) >= n else None


def annual_vol(xs: list[float], n: int = DAYS_1Y) -> float | None:
    if len(xs) <= n:
        return None
    r = [math.log(xs[i] / xs[i - 1]) for i in range(len(xs) - n, len(xs))]
    m = sum(r) / n
    return math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1)) * math.sqrt(252)


def median_traded_value(closes: list[float], volumes: list[int], n: int = 60) -> float | None:
    if len(closes) < n:
        return None
    return median(c * v for c, v in zip(closes[-n:], volumes[-n:]))


def beta_r2(stock: dict[date, float], index: dict[date, float], n: int = DAYS_1Y) -> tuple[float, float] | None:
    """Beta of the stock to the index and R2 (share of the stock's daily moves explained by the index)."""
    days = sorted(set(stock) & set(index))
    if len(days) <= n:
        return None
    days = days[-(n + 1):]
    rs = [stock[b] / stock[a] - 1 for a, b in zip(days, days[1:])]
    ri = [index[b] / index[a] - 1 for a, b in zip(days, days[1:])]
    ms, mi = sum(rs) / n, sum(ri) / n
    cov = sum((s - ms) * (i - mi) for s, i in zip(rs, ri))
    var_i = sum((i - mi) ** 2 for i in ri)
    var_s = sum((s - ms) ** 2 for s in rs)
    if var_i == 0 or var_s == 0:
        return None
    return cov / var_i, cov * cov / (var_i * var_s)
