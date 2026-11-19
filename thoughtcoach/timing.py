"""Time where it mattered: did you think longest in the positions that needed it?

A position is critical when only one move keeps your win chance (the review's candidates are the
moves within its margin of the best one: here there is just one). Obvious ones are left out: a
recapture of the piece that just took, or a position with at most two legal moves. Moves in time
trouble are left out too (no time to spend).

  rushed     a critical position where you lost COSTLY or more, playing faster than your usual
             think in that game (the median) or in under QUICK seconds
  lingered   a long think (at least LONG x your usual and LONG_MIN seconds) in a position where
             EASY or more moves were about equally good

No engine work: everything is read from the review's records.
"""

from __future__ import annotations

import re
import statistics

import chess

COSTLY = 0.10
QUICK = 10
LONG, LONG_MIN = 2.0, 30
EASY = 3


def _obvious(rec: dict) -> bool:
    board = chess.Board(rec["fen"])
    if board.legal_moves.count() <= 2:
        return True
    best = (rec.get("candidates") or [{}])[0].get("move_uci")
    opp = rec.get("opponent_move_san") or ""
    if not best or "x" not in opp:
        return False
    took = re.findall(r"[a-h][1-8]", opp)
    return bool(took) and best[2:4] == took[-1]  # taking back on the square they just took on


def positions(analysis: dict, report: dict) -> list[dict]:
    """One row per reviewed move of yours outside time trouble: its time, how many moves were
    good, and whether the position was critical."""
    tt = report.get("thresholds", {}).get("time_trouble", 30)
    costly = {m["ply"]: m for m in report.get("moves", [])}
    out = []
    for r in analysis.get("moves", []):
        if r.get("skipped") or not r.get("candidates") or r.get("time_spent") is None or r.get("wp_loss") is None:
            continue
        if r.get("clock_before") is not None and r["clock_before"] < tt:
            continue
        good = len(r["candidates"])
        obvious = _obvious(r)
        out.append({"ply": r["ply"], "move_number": r["move_number"], "move_san": r["move_san"], "fen": r["fen"],
                    "time": float(r["time_spent"]), "good": good, "best": r.get("best_move_san"),
                    "critical": good == 1 and not obvious, "obvious": obvious, "wp_loss": r["wp_loss"],
                    "wp_before": r.get("wp_before"), "wp_after": r.get("wp_after"), "gap": (costly.get(r["ply"]) or {}).get("gap")})
    return out


def game(analysis: dict, report: dict) -> dict | None:
    """This game's time against criticality, or None when there is too little to go on."""
    rows = [p for p in positions(analysis, report) if not p["obvious"]]  # recaptures say nothing about time
    crit = [p for p in rows if p["critical"]]
    other = [p for p in rows if not p["critical"]]
    if len(rows) < 6 or not crit or not other:
        return None
    usual = statistics.median(p["time"] for p in rows)
    rushed = sorted((p for p in crit if p["wp_loss"] >= COSTLY and (p["time"] <= usual or p["time"] < QUICK)),
                    key=lambda p: -p["wp_loss"])
    lingered = sorted((p for p in other if p["good"] >= EASY and p["time"] >= max(LONG * usual, LONG_MIN)),
                      key=lambda p: -p["time"])
    return {"usual": usual, "critical": len(crit), "positions": len(rows),
            "crit_median": statistics.median(p["time"] for p in crit),
            "other_median": statistics.median(p["time"] for p in other),
            "crit_times": [p["time"] for p in crit], "other_times": [p["time"] for p in other],
            "costly": sum(1 for p in rows if p["wp_loss"] >= COSTLY),
            "costly_critical": sum(1 for p in crit if p["wp_loss"] >= COSTLY),
            "rushed": rushed, "lingered": lingered, "plies": {p["ply"] for p in crit}}


def label(p: dict, white: bool) -> str:
    return f"{p['move_number']}. {p['move_san']}" if white else f"{p['move_number']}... {p['move_san']}"


def sentence(t: dict) -> str:
    """'Positions where only one move held got 12 s from you (median); the rest got 21 s.'"""
    c, o = t["crit_median"], t["other_median"]
    tail = ("about the same" if abs(c - o) <= 2 else "more time where it mattered" if c > o
            else "less time where it mattered")
    tail += (", but not every time." if t["rushed"] and c > o + 2 else ".")
    return (f"In the {t['critical']} position{'s' * (t['critical'] != 1)} where only one move held you took "
            f"{c:.0f} s (median); everywhere else {o:.0f} s: {tail}")
