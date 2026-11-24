"""How masters do it: your habits next to strong players' (master_baseline.json, when it's there).

The baseline is pooled from reviews of strong players' games, through this same review
(each player weighted equally, so it says "masters", not one person). It's only aggregate rates: no
names, games or words. Without the file everything here returns nothing and the review is as before.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import habits

DATA = Path(__file__).with_name("master_baseline.json")
LABEL = {  # (what is counted, said about you)
    "q3": "Real threats left unstopped",
    "q3_quiet": "Quiet threats named early",
    "q1": "Weaknesses pointed at",
    "candidates": "Best move among your candidates",
    "q2": "Least active pieces brought into play",
    "trust": "Good moves drawn, then dropped",
    "sharp": "Critical positions that went wrong",
    "time": "Moves in time trouble",
    "input": "Moves with an arrow or words",
}


@lru_cache(maxsize=1)
def baseline() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))["measures"]
    except (OSError, ValueError, KeyError):
        return {}


@lru_cache(maxsize=1)
def splits() -> dict:
    """Masters' habits split by situation (splits.py), when the baseline has them."""
    try:
        return json.loads(DATA.read_text(encoding="utf-8")).get("splits") or {}
    except (OSError, ValueError):
        return {}


def rate(key: str) -> float | None:
    return (baseline().get(key) or {}).get("rate")


def your_rates(games: list[dict]) -> dict[str, tuple[int, int]]:
    """Your counts for each habit across games (each with "report" and "analysis")."""
    out = {}
    for key, (_, count, _) in habits.MEASURE.items():
        k = n = 0
        for g in games:
            try:
                a, b = count(g)
            except Exception:
                continue
            k, n = k + a, n + b
        out[key] = (k, n)
    return out


def gaps(yours: dict[str, tuple[int, int]], min_n: int = 10) -> dict[str, float]:
    """How far behind masters you are on each habit, as a ratio (1 = level, 2 = twice as far off; under 1
    = better than masters). Only habits with min_n+ cases of yours and a master rate."""
    out = {}
    for key, (k, n) in yours.items():
        m = rate(key)
        if not m or n < min_n or key not in habits.MEASURE:
            continue
        mine = k / n
        up = habits.MEASURE[key][2]
        out[key] = (m / mine if mine else 3.0) if up else (mine / m)
    return out


def search(moves: list[dict]) -> dict:
    """Your search width over these moves: median candidates, share with 8+, and good moves dropped."""
    def drawn(m):
        return {x.get("move_san") for x in m.get("drawn_candidates") or []} - {None}

    mv = [m for m in moves if not m.get("skipped") and drawn(m)]
    if not mv:
        return {}
    n = sorted(len(drawn(m)) for m in mv)
    found = [m for m in mv if m.get("best_move_san") in drawn(m)]
    dropped = [m for m in found if m["move_san"] != m["best_move_san"] and (m.get("wp_loss") or 0) > 0.05]
    return {"cands": n[len(n) // 2], "many": sum(x >= 8 for x in n) / len(n),
            "dropped": len(dropped) / len(found) if found else None, "found": len(found)}


def card(yours: dict[str, tuple[int, int]], your_search: dict) -> str:
    """The analytics card: each habit, you against masters, as two bars."""
    if not baseline():
        return ""
    rows = []
    for key, label in LABEL.items():
        k, n = yours.get(key, (0, 0))
        m = rate(key)
        if not n or m is None:
            continue
        up = habits.MEASURE[key][2]
        mine = k / n
        better = mine >= m if up else mine <= m
        rows.append((label, mine, m, better, f"{k} of {n}"))
    if not rows:
        return ""
    top = max(max(a, b) for _, a, b, _, _ in rows) or 1
    body = "".join(
        f"<div class='vm' title='{label}: you {mine:.0%} ({n}), masters {m:.0%}'><span class='lab'>{label}</span>"
        f"<span class='trk'><i class='{'ok' if better else 'off'}' style='width:{mine / top * 100:.0f}%'></i>"
        f"<em style='left:{m / top * 100:.0f}%'></em></span><span class='val'><b>{mine:.0%}</b> <small>vs {m:.0%}</small></span></div>"
        for label, mine, m, better, n in rows)
    return (f"<div class='pcard vmcard'><h3>You and masters</h3><p class='cap'>Bar = you; line = masters (strong "
            f"players' games through this same review, each player counted equally). Green = level or "
            f"better.</p>{body}</div>")


CSS = """
.vm { display:grid; grid-template-columns:minmax(0, 1.5fr) minmax(0, 1fr) 84px; gap:8px; align-items:center; font-size:12.5px; margin:6px 0; }
.vm .lab { color:var(--fg2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.vm .trk { position:relative; height:10px; background:var(--s1-soft); border-radius:0 4px 4px 0; }
.vm .trk i { position:absolute; inset:0 auto 0 0; border-radius:0 4px 4px 0; min-width:2px; }
.vm .trk i.ok { background:var(--good); } .vm .trk i.off { background:var(--warn); }
.vm .trk em { position:absolute; top:-3px; bottom:-3px; width:2px; margin-left:-1px; background:var(--fg); border-radius:1px; }
.vm .val { text-align:right; font-variant-numeric:tabular-nums; } .vm .val small { color:var(--muted); }
@media (max-width:520px) { .vm { grid-template-columns:1fr 84px; row-gap:2px; } .vm .lab { grid-column:1 / -1; white-space:normal; } }
"""
