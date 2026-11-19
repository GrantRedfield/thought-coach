"""Comfortable, then caught: how you play when you're winning.

A common pattern: the position is good, attention drops, and the opponent's tactic lands. For your
moves outside time trouble, split by your win chance before the move:

  ahead    AHEAD or better
  level    between LEVEL and AHEAD

and compare how long you think and how often it goes wrong. A costly move while ahead is "caught"
when the engine's punishment starts with a capture or a check (their reply in the line after your
move). Comparisons carry how sure they are (bayes.py). No engine work: read from the review.
"""

from __future__ import annotations

import re
import statistics

from . import bayes

AHEAD, LEVEL = 0.65, 0.35
COSTLY = 0.10
MOVE_NO = re.compile(r"^\d+\.+")


def _reply(line: str | None) -> str:
    """Their reply in '27...Nxc3+ 28. Qxc3 Qxc3': the second move, numbers stripped."""
    moves = [MOVE_NO.sub("", t) for t in (line or "").split()]
    moves = [t for t in moves if t]
    return moves[1] if len(moves) > 1 else ""


def rows(analysis: dict, report: dict) -> list[dict]:
    tt = report.get("thresholds", {}).get("time_trouble", 30)
    out = []
    for r in analysis.get("moves", []):
        if r.get("skipped") or r.get("wp_before") is None or r.get("wp_loss") is None or r.get("time_spent") is None:
            continue
        if r.get("clock_before") is not None and r["clock_before"] < tt:
            continue
        reply = _reply(r.get("played_line_san"))
        out.append({"ply": r["ply"], "move_number": r["move_number"], "move_san": r["move_san"],
                    "wp_before": r["wp_before"], "wp_after": r.get("wp_after"), "wp_loss": r["wp_loss"],
                    "time": float(r["time_spent"]), "costly": r["wp_loss"] >= COSTLY, "reply": reply,
                    "tactic": "x" in reply or "+" in reply or "#" in reply,
                    "zone": "ahead" if r["wp_before"] >= AHEAD else "level" if r["wp_before"] >= LEVEL else "behind"})
    return out


def caught(analysis: dict, report: dict) -> list[dict]:
    """This game's costly moves made while ahead, worst first."""
    return sorted((r for r in rows(analysis, report) if r["zone"] == "ahead" and r["costly"]),
                  key=lambda r: -r["wp_loss"])


def summary(rs: list[dict]) -> dict | None:
    """Ahead against level, for these moves (one game or many)."""
    ahead = [r for r in rs if r["zone"] == "ahead"]
    level = [r for r in rs if r["zone"] == "level"]
    if len(ahead) < 5 or len(level) < 5:
        return None
    usual = statistics.median(r["time"] for r in rs)
    fast = lambda grp: sum(r["time"] < usual for r in grp)  # noqa: E731
    ka, kl = sum(r["costly"] for r in ahead), sum(r["costly"] for r in level)
    pooled = (ka + kl) / (len(ahead) + len(level))
    return {
        "ahead": len(ahead), "level": len(level),
        "think_ahead": statistics.median(r["time"] for r in ahead),
        "think_level": statistics.median(r["time"] for r in level),
        # the chance you really rush more when ahead (moves faster than your usual think)
        "p_faster": bayes.prob_greater_prior(fast(ahead), len(ahead), fast(level), len(level),
                                             bayes.sceptic((fast(ahead) + fast(level)) / (len(ahead) + len(level)))),
        "costly_ahead": ka, "costly_level": kl,
        "p_costlier": bayes.prob_greater_prior(ka, len(ahead), kl, len(level), bayes.sceptic(pooled or 0.1)),
        "caught": [r for r in ahead if r["costly"]],
        "by_tactic": sum(r["tactic"] for r in ahead if r["costly"]),
    }


def sentence(s: dict) -> str:
    """'You think 4 s a move when you're ahead and 7 s when it's level; ...'"""
    head = (f"You think {s['think_ahead']:.0f} s a move when you're ahead (65%+) and {s['think_level']:.0f} s "
            f"when it's level")
    ra, rl = s["costly_ahead"] / s["ahead"], s["costly_level"] / s["level"]
    rates = f"{ra:.0%} of your moves go wrong when ahead, {rl:.0%} when level"
    if s["p_costlier"] >= bayes.LIKELY:
        tail = f". And {rates}: {bayes.verdict(s['p_costlier'])}."
    elif s["p_costlier"] >= 0.6:
        tail = f". {rates.capitalize()}: {bayes.verdict(s['p_costlier'])} ({bayes.odds(s['p_costlier'])})."
    else:
        tail = f". {rates.capitalize()}: no real difference, but a slip while winning costs a lot."
    fast = (f" Playing faster when ahead: {bayes.verdict(s['p_faster'])} ({bayes.odds(s['p_faster'])})."
            if s["think_ahead"] < s["think_level"] else "")
    return head + tail + fast
