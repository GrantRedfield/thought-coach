"""Small-sample honesty: rates as Beta posteriors instead of bare percentages.

A rate seen as k of n (threats spotted, costly moves...) is uncertain when n is small. With a flat
Beta(1, 1) prior the posterior is Beta(k + 1, n - k + 1). From it:

  interval(k, n)          the range the true rate is in with 90% probability ("likely 21-58%")
  prob_greater(a, b)      the chance one rate is really higher than another (exact, no sampling)
  prob_above(k, n, x)     the chance a rate is really above x
  sure(p)                 that chance in words ("likely", "too close to call")

Pure Python (math.lgamma and a continued fraction), so the review needs nothing new installed,
and the same numbers always give the same answer.
"""

from __future__ import annotations

import math

LEVEL = 0.90  # credible intervals
LIKELY = 0.80  # a comparison is stated as a finding from this chance on
VERY = 0.95


def _lbeta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes, betacf)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """P(X <= x) for X ~ Beta(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - _lbeta(a, b))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def _quantile(q: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def interval(k: int, n: int, level: float = LEVEL) -> tuple[float, float]:
    """The central `level` credible interval for a rate seen k times in n (flat prior)."""
    a, b = k + 1, n - k + 1
    tail = (1 - level) / 2
    return _quantile(tail, a, b), _quantile(1 - tail, a, b)


def prob_greater(k1: int, n1: int, k2: int, n2: int) -> float:
    """P(rate 1 > rate 2), exact for Beta posteriors (Evan Miller's sum)."""
    a1, b1, a2, b2 = k1 + 1, n1 - k1 + 1, k2 + 1, n2 - k2 + 1
    # P(p1 > p2) = sum_{i=0}^{a1-1} B(a2 + i, b1 + b2) / ((b1 + i) B(1 + i, b1) B(a2, b2))
    total = 0.0
    for i in range(a1):
        total += math.exp(_lbeta(a2 + i, b1 + b2) - math.log(b1 + i) - _lbeta(1 + i, b1) - _lbeta(a2, b2))
    return min(1.0, max(0.0, total))


def prob_greater_prior(k1: int, n1: int, k2: int, n2: int, prior: tuple[float, float]) -> float:
    """P(rate 1 > rate 2) with a shared Beta(a0, b0) prior (any strength): a sceptical prior pulls
    both rates toward the same value, so a handful of moves can't look like a sure thing.
    Numerical: the integral of f1(x) * F2(x) over [0, 1]."""
    a0, b0 = prior
    a1, b1, a2, b2 = k1 + a0, n1 - k1 + b0, k2 + a0, n2 - k2 + b0
    steps, total = 400, 0.0
    lb = _lbeta(a1, b1)
    for i in range(steps):
        x = (i + 0.5) / steps
        pdf = math.exp((a1 - 1) * math.log(x) + (b1 - 1) * math.log(1 - x) - lb)
        total += pdf * beta_cdf(x, a2, b2) / steps
    return min(1.0, max(0.0, total))


def sceptic(rate: float, strength: float = 8.0) -> tuple[float, float]:
    """A prior worth `strength` typical moves at the overall rate."""
    rate = min(max(rate, 0.02), 0.98)
    return rate * strength, (1 - rate) * strength


def prob_above(k: int, n: int, x: float) -> float:
    """P(rate > x) for a rate seen k times in n."""
    return 1 - beta_cdf(x, k + 1, n - k + 1)


def chance(p: float) -> str:
    """A probability for people: '84%', and never a flat 0% or 100% ('over 99%', 'under 1%')."""
    if p >= 0.995:
        return "over 99%"
    if p <= 0.005:
        return "under 1%"
    return f"{p * 100:.0f}%"


def sure(p: float) -> str:
    """How sure, in words, that something is real (p = its probability)."""
    if p >= VERY:
        return "very likely"
    if p >= LIKELY:
        return "likely"
    if p >= 0.65:
        return "leaning that way"
    return "too close to call"


def span(k: int, n: int) -> str:
    """'likely 21–58%' for a rate seen k times in n."""
    lo, hi = interval(k, n)
    return f"likely {lo * 100:.0f}–{hi * 100:.0f}%"
