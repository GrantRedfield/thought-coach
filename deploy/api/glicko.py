"""Glicko-2 (Glickman, "Example of the Glicko-2 system"), one game at a time, as lichess rates puzzles: a
puzzle attempt is a game between the player and the puzzle, a solve a win for the player.

Ratings are on the Glicko scale (1500 / RD 350 to start). A player who has been away is less certain:
their RD grows with the days since their last attempt before the update.
"""

from __future__ import annotations

import math

SCALE = 173.7178
TAU = 0.5          # how fast volatility may change
START = {"r": 1500.0, "rd": 350.0, "vol": 0.06}
RD_MIN, RD_MAX = 45.0, 350.0
DAY = 86400


def _g(phi: float) -> float:
    return 1 / math.sqrt(1 + 3 * phi * phi / math.pi ** 2)


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def aged(p: dict, now: float) -> dict:
    """The rating as it stands now: RD grows by one rating period per day away."""
    p = {**START, **p}
    days = max(0.0, (now - p.get("t", now)) / DAY)
    if days:
        phi, sigma = p["rd"] / SCALE, p["vol"]
        p["rd"] = min(RD_MAX, math.sqrt(phi * phi + days * sigma * sigma) * SCALE)
    return p


def update(p: dict, opp: dict, score: float) -> dict:
    """p's new rating after one game against opp (score 1 win, 0 loss, 0.5 draw)."""
    p, opp = {**START, **p}, {**START, **opp}
    mu, phi, sigma = (p["r"] - 1500) / SCALE, p["rd"] / SCALE, p["vol"]
    mu_j, phi_j = (opp["r"] - 1500) / SCALE, opp["rd"] / SCALE
    g, e = _g(phi_j), _e(mu, mu_j, phi_j)
    v = 1 / (g * g * e * (1 - e))
    delta = v * g * (score - e)

    # new volatility (Illinois algorithm, step 5 of the paper)
    a = math.log(sigma * sigma)
    f = lambda x: (math.exp(x) * (delta ** 2 - phi ** 2 - v - math.exp(x)) / (2 * (phi ** 2 + v + math.exp(x)) ** 2)  # noqa: E731
                   - (x - a) / TAU ** 2)
    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU
    fa, fb = f(A), f(B)
    for _ in range(100):
        if abs(B - A) <= 1e-6:
            break
        C = A + (A - B) * fa / (fb - fa)
        fc = f(C)
        if fc * fb <= 0:
            A, fa = B, fb
        else:
            fa /= 2
        B, fb = C, fc
    sigma2 = math.exp(A / 2)

    phi_star = math.sqrt(phi * phi + sigma2 * sigma2)
    phi2 = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
    mu2 = mu + phi2 * phi2 * g * (score - e)
    return {"r": round(mu2 * SCALE + 1500, 1), "rd": round(min(RD_MAX, max(RD_MIN, phi2 * SCALE)), 1), "vol": round(sigma2, 6)}


def expected(p: dict, opp: dict) -> float:
    p, opp = {**START, **p}, {**START, **opp}
    return _e((p["r"] - 1500) / SCALE, (opp["r"] - 1500) / SCALE, opp["rd"] / SCALE)
