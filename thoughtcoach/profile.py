"""Patterns across all your reviewed games: things a single game is too small to show.

Everything is counted from the saved reviews in `out/` (gap reports, engine analysis) and the
PGNs in `games/`; nothing new is computed by the engine. Each pattern says how many moves it rests
on, and anything resting on fewer than SOLID moves is marked as an early signal.

Rows are your reviewed moves (book and already-decided moves are left out). Time-trouble moves
(under the time-trouble threshold on your clock) are left out of the thinking patterns, because
there is no time to think there, and counted separately.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import chess
import chess.pgn
from . import bayes, questions, tone
from .fmt import game_url, went

SOLID = 30  # moves a comparison needs before it is more than an early signal
PHASES = [("Opening (to move 15)", 0, 15), ("Middlegame (16-30)", 16, 30), ("Late game (31+)", 31, 999)]
THINK = [("under 3s", 0, 3), ("3-8s", 3, 8), ("8-20s", 8, 20), ("20s+", 20, 1e9)]
PIECES = {"N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king", "": "pawn", "O": "castling"}


def _piece(san: str) -> str:
    return PIECES.get(san[0] if san[0] in "NBRQKO" else "", "pawn")


def _opp_clocks(pgn: Path, me: chess.Color) -> dict[int, float]:
    if not pgn.exists():
        return {}
    from .analyse import opponent_clocks

    with open(pgn, encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    return opponent_clocks(game, me) if game else {}


def _played(pgn: Path) -> str | None:
    """When the game started (UTC), from the PGN headers: '2026.11.17 20:16:53'."""
    if not pgn.exists():
        return None
    head = pgn.read_text(encoding="utf-8", errors="replace")[:2000]
    d, t = re.search(r'\[UTCDate "([^"]+)"\]', head), re.search(r'\[UTCTime "([^"]+)"\]', head)
    return f"{d.group(1)} {t.group(1) if t else ''}".strip() if d else None


def load(out: Path, games: Path) -> list[dict]:
    """Every reviewed game: its report, analysis records and opponent clocks, oldest first."""
    found = []
    for path in out.glob("*.gaps.json"):
        gid = path.name.split(".")[0]
        analysis_path = out / f"{gid}.json"
        if not analysis_path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        me = chess.WHITE if report["game"]["my_color"] == "white" else chess.BLACK
        found.append({"id": gid, "report": report, "analysis": analysis,
                      "opp": _opp_clocks(games / f"{gid}.pgn", me), "mtime": path.stat().st_mtime,
                      "played": _played(games / f"{gid}.pgn") or report["game"].get("date") or ""})
    found.sort(key=lambda g: (g["played"], g["mtime"]))
    return found


def _rate(rows: list[dict]) -> dict:
    n = len(rows)
    costly = sum(r["costly"] for r in rows)
    return {"n": n, "costly": costly, "rate": costly / n if n else 0.0,
            "loss": statistics.mean(r["loss"] for r in rows) if n else 0.0,
            "before": statistics.mean(r["before"] for r in rows) if n else 0.0,
            "after": statistics.mean(r["after"] for r in rows) if n else 0.0}


def compute(games: list[dict]) -> dict:
    rows, per_game = [], []
    gap_loss: dict[str, float] = {}
    q = {"q1": [0, 0], "q2": [0, 0], "q3": [0, 0], "q4": [0, 0]}
    q3_handled = 0
    clock_diff: dict[int, list[float]] = {}
    tt_moves = []
    for g in games:
        r, a = g["report"], g["analysis"]
        tt = r["thresholds"].get("time_trouble", 30)
        s = r["summary"]
        thinking = s.get("moves_with_thoughts", 0) > 0
        moves = [m for m in r["moves"] if not m.get("skipped")]
        for m in moves:
            in_tt = m.get("clock_before") is not None and m["clock_before"] < tt
            row = {"game": g["id"], "move": m["move_number"], "loss": m["wp_loss"], "costly": bool(m["mistake"]),
                   "before": m["wp_before"], "after": m["wp_after"],
                   "time": m.get("own_time_s") or m.get("time_spent_pgn") or 0, "piece": _piece(m["move_san"]),
                   "cands": len([d for d in m.get("drawn_candidates", []) if d.get("legal")]),
                   "good": any(d.get("legal") and d.get("engine_candidate") for d in m.get("drawn_candidates", [])),
                   "thinking": thinking, "tt": in_tt}
            (tt_moves if in_tt else rows).append(row)
            if m["mistake"]:
                gap_loss[m["gap"]] = gap_loss.get(m["gap"], 0) + m["wp_loss"]
        for rec in a["moves"]:
            opp = g["opp"].get(rec["ply"])
            if rec.get("clock_before") is not None and opp is not None:
                clock_diff.setdefault(rec["move_number"], []).append(rec["clock_before"] - opp)
        if "q1_weaknesses" in s:
            q["q1"][0] += s["q1_found"]; q["q1"][1] += s["q1_weaknesses"]  # noqa: E702
            q["q2"][0] += s["q2_found"]; q["q2"][1] += s["q2_positions"]  # noqa: E702
        if thinking:
            # Seen = drawn before it could happen; threats your move happened to stop are kept apart.
            q["q3"][0] += s["q3_spotted"]; q["q3"][1] += s["q3_real_threats"]  # noqa: E702
            q3_handled += s.get("q3_handled", 0)
            q["q4"][0] += s["q4_best_considered"]; q["q4"][1] += s["q4_positions"]  # noqa: E702
        first_tt = next((m["move_number"] for m in moves if m.get("clock_before") is not None and m["clock_before"] < tt), None)
        per_game.append({
            "id": g["id"], "date": r["game"].get("date"), "color": r["game"]["my_color"], "result": r["game"]["result"],
            "tc": r["game"].get("time_control"), "moves": len(moves),
            "lost": sum(m["wp_loss"] for m in moves), "costly": sum(m["mistake"] for m in moves),
            "per_move": statistics.mean(m["wp_loss"] for m in moves) if moves else 0, "first_tt": first_tt,
            "before": statistics.mean(m["wp_before"] for m in moves) if moves else 0,
            "after": statistics.mean(m["wp_after"] for m in moves) if moves else 0,
            "won": (r["game"]["result"] == "1-0") == (r["game"]["my_color"] == "white") and r["game"]["result"] != "1/2-1/2",
            "strike": r.get("strike"),
        })

    thinking_rows = [x for x in rows if x["thinking"]]
    total_loss = sum(x["loss"] for x in rows + tt_moves)
    strikes = {"games": 0, "you": {"n": 0, "won": 0}, "them": {"n": 0, "won": 0}, "none": {"n": 0, "won": 0},
               "door_you": 0, "door_them": 0, "blunder_you": 0, "blunder_them": 0}
    for g in per_game:
        st = g["strike"]
        if not st:
            continue
        strikes["games"] += 1
        key = st.get("side") or "none"
        strikes[key]["n"] += 1
        strikes[key]["won"] += g["won"]
        door = st.get("door") or {}
        strikes["door_you"] += door.get("side") == "you"
        strikes["door_them"] += door.get("side") == "them"
        strikes["blunder_you"] += st.get("decided_side") == "you"
        strikes["blunder_them"] += st.get("decided_side") == "them"
    return {
        "strikes": strikes,
        "q3_handled": q3_handled,
        "games": per_game,
        "moves": len(rows) + len(tt_moves),
        "all": _rate(rows),
        "phase": [(name, _rate([x for x in rows if lo <= x["move"] <= hi])) for name, lo, hi in PHASES],
        "think": [(name, _rate([x for x in rows if lo <= x["time"] < hi])) for name, lo, hi in THINK],
        "cands": [(name, _rate([x for x in thinking_rows if test(x["cands"])]))
                  for name, test in (("none", lambda c: c == 0), ("one", lambda c: c == 1),
                                     ("two or more", lambda c: c >= 2))],
        "good": [(name, _rate([x for x in thinking_rows if x["cands"] and x["good"] == want]))
                 for name, want in (("yes", True), ("no", False))],
        "aagaard": questions.compute(games),
        "tone": {"stats": tone.stats([r for g in games for r in (g["report"].get("tone") or {}).get("moves", [])],
                                     tone.MIN_MOVES_ALL),
                 "games": sum(1 for g in games if (g["report"].get("tone") or {}).get("moves"))},
        "piece": sorted(((p, _rate([x for x in rows if x["piece"] == p])) for p in {x["piece"] for x in rows}),
                        key=lambda kv: -kv[1]["n"]),
        "tt": {"moves": len(tt_moves), "loss": sum(x["loss"] for x in tt_moves),
               "share": sum(x["loss"] for x in tt_moves) / total_loss if total_loss else 0,
               "games": sum(1 for g in per_game if g["first_tt"]), "first": [g["first_tt"] for g in per_game if g["first_tt"]]},
        "gaps": sorted(gap_loss.items(), key=lambda kv: -kv[1]),
        "questions": q,
        "clock": sorted((n, statistics.mean(v), len(v)) for n, v in clock_diff.items() if len(v) >= 2),
        "color": {c: _rate([x for x in rows if next(g for g in per_game if g["id"] == x["game"])["color"] == c])
                  for c in ("white", "black")},
    }


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def findings(p: dict) -> tuple[list[dict], list[dict]]:
    """(strong points, weak points): sentences with the numbers and sample sizes behind them,
    strongest first. Each is a comparison that only shows up across many moves."""
    strong, weak = [], []

    def add(lst, score, head, body, n, sure=None):
        """sure: the chance the difference behind it is real (bayes.py); under LIKELY it isn't stated."""
        if sure is not None and sure < bayes.LIKELY:
            return
        lst.append({"score": score, "head": head, "body": body, "n": n, "early": n < SOLID, "sure": sure})

    prior = bayes.sceptic(p["all"]["rate"] if p["all"]["n"] else 0.25)  # every group starts out like you

    def more(a: dict, b: dict) -> float:
        """The chance group a really goes wrong more often than group b."""
        return bayes.prob_greater_prior(a["costly"], a["n"], b["costly"], b["n"], prior)

    # Think time: do quick moves go wrong more often?
    think = {k: v for k, v in p["think"]}
    fast, slow = think["under 3s"], _merge(think["8-20s"], think["20s+"])
    if fast["n"] >= 10 and slow["n"] >= 10:
        pf = more(fast, slow)
        if pf >= bayes.LIKELY:
            add(weak, fast["rate"] - slow["rate"], "Quick moves go wrong",
                f"{_pct(fast['rate'])} of moves played in under 3s were costly, against {_pct(slow['rate'])} "
                f"when you took 8s or more.", min(fast["n"], slow["n"]), pf)
        elif pf <= 1 - bayes.LIKELY:
            add(weak, slow["rate"] - fast["rate"], "Long thinks go wrong",
                f"{_pct(slow['rate'])} of moves you thought 8s+ about were costly, against {_pct(fast['rate'])} "
                f"for quick ones: the hard positions are where it slips.", min(fast["n"], slow["n"]), 1 - pf)
        elif fast["rate"] <= slow["rate"]:
            add(strong, 0.05, "Your quick moves hold up",
                f"Moves played in under 3s were costly {_pct(fast['rate'])} of the time, no worse than your long "
                f"thinks ({_pct(slow['rate'])}).", min(fast["n"], slow["n"]))

    # Candidates: one idea against two or more.
    cands = {k: v for k, v in p["cands"]}
    one, two = cands["one"], cands["two or more"]
    if one["n"] >= 8 and two["n"] >= 8:
        p1 = more(one, two)
        if p1 >= bayes.LIKELY:
            add(weak, one["rate"] - two["rate"], "One candidate isn't enough",
                f"When you considered a single move, {_pct(one['rate'])} were costly; with two or more considered, "
                f"{_pct(two['rate'])}.", min(one["n"], two["n"]), p1)
        elif two["rate"] <= one["rate"]:
            add(strong, 0.04, "Comparing moves pays off",
                f"With two or more moves considered you went wrong {_pct(two['rate'])} of the time "
                f"({two['n']} moves).", two["n"])

    # Game phase.
    phases = [(name, r) for name, r in p["phase"] if r["n"] >= 8]
    if len(phases) >= 2:
        worst = max(phases, key=lambda kv: kv[1]["loss"])
        best = min(phases, key=lambda kv: kv[1]["loss"])
        pp = more(worst[1], best[1])
        if worst[1]["loss"] >= 1.5 * best[1]["loss"] and pp >= bayes.LIKELY:
            add(weak, worst[1]["loss"] * 5, f"{worst[0].split(' (')[0]} is where it goes",
                f"Your average move there went {went(worst[1]['before'], worst[1]['after'])}, against "
                f"{went(best[1]['before'], best[1]['after'])} in the {best[0].split(' (')[0].lower()}.", worst[1]["n"], pp)
            add(strong, 0.06, f"Solid {best[0].split(' (')[0].lower()}",
                f"Your average move there went {went(best[1]['before'], best[1]['after'])}, over {best[1]['n']} moves.",
                best[1]["n"])

    # The clock.
    tt = p["tt"]
    if tt["games"] >= 2 and tt["share"] >= 0.25:
        first = statistics.median(tt["first"])
        add(weak, tt["share"], "The clock costs you most",
            f"{_pct(tt['share'])} of everything you lost came in time trouble, in {tt['games']} of "
            f"{len(p['games'])} games; you typically drop under 30s around move {first:.0f}.", tt["moves"] + 10)
    behind = [(n, d) for n, d, k in p["clock"] if 15 <= n <= 30]
    if len(behind) >= 5:
        avg = statistics.mean(d for _, d in behind)
        if avg <= -15:
            add(weak, min(0.3, -avg / 200), "Behind on the clock in the middlegame",
                f"Between moves 15 and 30 you have {-avg:.0f}s less than your opponent on average.", len(behind) * 3)
        elif avg >= 15:
            add(strong, 0.05, "Ahead on the clock", f"Between moves 15 and 30 you have {avg:.0f}s more than "
                                                    f"your opponent on average.", len(behind) * 3)

    # Aagaard's questions and candidates, across games.
    names = {"q1": "the weaknesses", "q2": "your worst pieces",
             "q4": "positions with the engine's best move among yours"}
    found3, real3 = p["questions"]["q3"]
    p3 = 1 - bayes.prob_above(found3, real3, 0.5) if real3 else 0
    if real3 >= 5 and found3 / real3 < 0.5 and p3 >= bayes.LIKELY:
        stopped = p.get("q3_handled", 0)
        add(weak, (0.5 - found3 / real3) / 2, "Threats seen too late",
            f"You spotted only {_pct(found3 / real3)} of the real threats against you before they could happen "
            f"({found3} of {real3}, {bayes.span(found3, real3)})" + (f"; your move happened to stop another {stopped}"
                                                                      if stopped else "") + ".", real3, p3)
    rates = {k: (f / n, n) for k, (f, n) in p["questions"].items() if n >= 5 and k in names}
    if rates:
        hi = max(rates, key=lambda k: rates[k][0])
        lo = min(rates, key=lambda k: rates[k][0])
        k_hi, k_lo = p["questions"][hi][0], p["questions"][lo][0]
        if rates[hi][0] >= 0.5:  # under half isn't a strength
            add(strong, rates[hi][0] / 4, f"Strongest question: {names[hi].split(' (')[0].replace('the ', '')}",
                f"You saw {_pct(rates[hi][0])} of {names[hi]} ({k_hi} of {rates[hi][1]}, "
                f"{bayes.span(k_hi, rates[hi][1])}).", rates[hi][1], bayes.prob_above(k_hi, rates[hi][1], 0.5))
        if lo != hi and rates[lo][0] < 0.5:
            add(weak, (0.5 - rates[lo][0]) / 2, f"Blind spot: {names[lo].replace('the ', '').replace('your ', '')}",
                f"You pointed at only {_pct(rates[lo][0])} of {names[lo]} ({k_lo} of {rates[lo][1]}, "
                f"{bayes.span(k_lo, rates[lo][1])}).", rates[lo][1], 1 - bayes.prob_above(k_lo, rates[lo][1], 0.5))

    # Pieces.
    pieces = [(k, v) for k, v in p["piece"] if v["n"] >= 10 and k != "castling"]
    if len(pieces) >= 3:
        worst = max(pieces, key=lambda kv: kv[1]["rate"])
        best = min(pieces, key=lambda kv: kv[1]["rate"])
        rest = {"costly": p["all"]["costly"] - worst[1]["costly"], "n": p["all"]["n"] - worst[1]["n"]}
        pw = more(worst[1], rest) if rest["n"] else 0
        if worst[1]["rate"] >= 1.5 * p["all"]["rate"] and pw >= bayes.LIKELY:
            add(weak, worst[1]["rate"] - p["all"]["rate"], f"Careful with the {worst[0]}",
                f"{_pct(worst[1]['rate'])} of your {worst[0]} moves were costly, against {_pct(p['all']['rate'])} "
                f"overall.", worst[1]["n"], pw)
        if best[1]["rate"] <= 0.5 * p["all"]["rate"]:
            add(strong, 0.04, f"Sure-handed with the {best[0]}",
                f"Only {_pct(best[1]['rate'])} of {best[1]['n']} {best[0]} moves were costly.", best[1]["n"])

    # Colour.
    w, b = p["color"]["white"], p["color"]["black"]
    if w["n"] >= 15 and b["n"] >= 15 and abs(w["loss"] - b["loss"]) >= 0.01:
        better, worse = ("white", "black") if w["loss"] < b["loss"] else ("black", "white")
        add(strong, 0.03, f"Better with {better}",
            f"Your average move with {better} went {went(p['color'][better]['before'], p['color'][better]['after'])}; "
            f"with {worse} {went(p['color'][worse]['before'], p['color'][worse]['after'])}.",
            min(w["n"], b["n"]))

    # Who struck first (strike.py), counted in games.
    st = p["strikes"]
    if st["games"] >= 4:
        if st["door_you"] >= 2 and st["door_you"] / st["games"] >= 0.3:
            weak.append({"score": 0.04 * st["door_you"], "head": "You open the door",
                         "body": f"In {st['door_you']} of {st['games']} games the first tactic came straight after a move "
                                 f"of yours that allowed it.", "n": st["games"], "unit": "games", "early": st["games"] < 10})
        if st["you"]["n"] >= 2 and st["you"]["won"] / st["you"]["n"] > (st["them"]["won"] / st["them"]["n"] if st["them"]["n"] else 0):
            strong.append({"score": 0.03 * st["you"]["n"], "head": "Striking first pays",
                           "body": f"When you struck first you won {st['you']['won']} of {st['you']['n']}; when they did, "
                                   f"{st['them']['won']} of {st['them']['n']}.", "n": st["games"], "unit": "games",
                           "early": st["games"] < 10})

    strong.sort(key=lambda f: (f["early"], -f["score"]))
    weak.sort(key=lambda f: (f["early"], -f["score"]))
    return strong[:5], weak[:5]


def _merge(a: dict, b: dict) -> dict:
    n = a["n"] + b["n"]
    costly = a["costly"] + b["costly"]
    return {"n": n, "costly": costly, "rate": costly / n if n else 0.0,
            "loss": (a["loss"] * a["n"] + b["loss"] * b["n"]) / n if n else 0.0}


# ---------------------------------------------------------------- page section


def _e(x) -> str:
    import html

    return html.escape(str(x), quote=True)


CSS = bayes.RANGE_CSS + """
.profile h2 { font-size:15px; margin:26px 0 4px; } .profile .sub { color:var(--fg2); margin:0 0 12px; font-size:13px; }
.pkpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin-bottom:12px; }
.pkpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px 12px; }
.pkpi b { display:block; font-size:22px; font-variant-numeric:tabular-nums; } .pkpi span { color:var(--fg2); font-size:12px; }
.findings { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px; }
.fcol { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }
.fcol h3 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--fg2); display:flex; gap:8px; align-items:center; }
.fcol h3 i { font-style:normal; width:18px; height:18px; border-radius:50%; display:grid; place-items:center; color:#fff; font-size:11px; }
.fcol.good h3 i { background:var(--good); } .fcol.weak h3 i { background:var(--warn); }
.fcol p { margin:6px 0; font-size:13.5px; } .fcol p b { display:block; } .fcol .n { color:var(--muted); font-size:11.5px; }
.early { font-size:10.5px; color:var(--muted); border:1px solid var(--line); border-radius:8px; padding:0 6px; margin-left:4px; }
.pgrid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:10px; }
.pcard { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }
.tone-find { margin:8px 0 0; font-size:13.5px; }
.pcard h3 { margin:0 0 2px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--fg2); font-weight:600; }
.pcard .cap { margin:0 0 8px; color:var(--muted); font-size:12px; }
.hb { display:grid; grid-template-columns:150px 1fr 70px; gap:6px; align-items:center; font-size:12.5px; margin:5px 0; }
.hb .lab { color:var(--fg2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hb .trk { height:10px; background:var(--s1-soft); border-radius:0 4px 4px 0; }
.hb .bar { height:10px; background:var(--s1); border-radius:0 4px 4px 0; min-width:2px; }
.hb .bar.muted { background:var(--bar-tt); } .hb .val { text-align:right; font-variant-numeric:tabular-nums; }
.hb .val small { color:var(--muted); }
.sb { display:grid; grid-template-columns:96px 1fr 104px; gap:8px; align-items:center; font-size:12.5px; margin:6px 0; }
.sb .lab { color:var(--fg2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sb .trk { display:flex; height:12px; background:var(--s1-soft); border-radius:4px; overflow:hidden; }
.sb .bad { display:block; height:100%; background:var(--s2); border-right:2px solid var(--card); min-width:2px; }
.sb .won { display:block; height:100%; background:var(--s1); border-right:2px solid var(--card); }
.sb .lost { display:block; height:100%; background:var(--s2); }
.sb .val { text-align:right; font-variant-numeric:tabular-nums; color:var(--fg2); } .sb .val b { color:var(--fg); }
.sb .val small { color:var(--muted); }
.pcard h4 { font-size:12px; font-weight:600; color:var(--fg2); margin:12px 0 2px; }
.pcard .key { font-size:11.5px; color:var(--muted); margin:-4px 0 6px; display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
.pcard .key i { display:inline-block; width:10px; height:10px; border-radius:2px; }
.pcard .key .k-bad { background:var(--s2); } .pcard .key .k-ok { background:var(--s1-soft); margin-left:6px; }
svg.pline { width:100%; height:auto; } .pline .ax { fill:var(--muted); font-size:10px; } .pline .grid { stroke:var(--line); }
.pline .zero { stroke:var(--muted); stroke-dasharray:3 3; } .pline .ln { fill:none; stroke:var(--s1); stroke-width:2; }
.pline .dot { fill:var(--s1); stroke:var(--card); stroke-width:2; } .pline .dot.lost { fill:var(--s2); }
@media (max-width: 700px) { .findings { grid-template-columns:1fr; } }
""" + questions.CSS


def _hbars(items: list[tuple[str, float, str, str]], scale: float | None = None, muted: set | None = None) -> str:
    """Horizontal bars: (label, value, shown value, tooltip)."""
    top = scale or max([v for _, v, _, _ in items] + [1e-9])
    out = []
    for label, value, shown, tip in items:
        cls = "bar muted" if muted and label in muted else "bar"
        out.append(f"<div class='hb' title='{_e(tip)}'><span class='lab'>{_e(label)}</span><span class='trk'>"
                   f"<span class='{cls}' style='display:block;width:{min(100, value / top * 100):.1f}%'></span></span>"
                   f"<span class='val'>{shown}</span></div>")
    return "".join(out)


def _tone_card(t: dict) -> str:
    """Your voice and your play (tone.py): each mood's moves, how often they went wrong and where
    your average move took your win chance; a finding only when there is enough behind it."""
    st = t["stats"]
    if not t["games"] or not st["talked"]:
        return "<p class='cap'>Shows up once games with your voice have been reviewed.</p>"
    names = {"upbeat": "Sounding upbeat", "worried": "Sounding worried", "neutral": "Calculating", "quiet": "Quiet"}
    rows = []
    for k in ("upbeat", "worried", "neutral", "quiet"):
        r = st["by"][k]
        if not r["n"]:
            continue
        thin = f" <small>({bayes.span(r['costly'], r['n'])})</small>"
        rows.append(f"<div class='sb' title='{_e(names[k])}: {r['n']} moves, {r['costly']} costly; average move "
                    f"{went(r['before'], r['after'])}'><span class='lab'>{names[k]}</span><span class='trk'>"
                    f"<span class='bad' style='width:{r['rate'] * 100:.1f}%'></span></span>"
                    f"<span class='val'><b>{_pct(r['rate'])}</b> costly <small>of {r['n']}</small>{thin}</span></div>")
    up, wo = st["by"]["upbeat"]["n"], st["by"]["worried"]["n"]
    verdict = "".join(f"<p class='tone-find'>{_e(f)}</p>" for f in st["findings"])
    verdict += "".join(f"<p class='cap'>{_e(f)}</p>" for f in st.get("notes", []))
    if not verdict:
        verdict = (f"<p class='cap'>Not enough yet to compare: {up} upbeat and {wo} worried moves so far, from "
                   f"{t['games']} game{'s' * (t['games'] != 1)} with your voice.</p>")
    return "".join(rows) + verdict


def _rate_bars(groups: list[tuple[str, dict]], labels: dict | None = None) -> str:
    """One bar per group: the whole bar is every move in the group, the orange part the costly ones
    (same 0-100% scale for every row, so the orange lengths compare directly)."""
    out = []
    for name, r in groups:
        if not r["n"]:
            continue
        label = (labels or {}).get(name, name)
        tip = f"{label}: {r['costly']} of {r['n']} moves costly ({_pct(r['rate'])}); average move {went(r['before'], r['after'])}"
        out.append(f"<div class='sb' title='{_e(tip)}'><span class='lab'>{_e(label)}</span><span class='trk'>"
                   f"<span class='bad' style='width:{r['rate'] * 100:.1f}%'></span></span>"
                   f"<span class='val'><b>{_pct(r['rate'])}</b> costly <small>of {r['n']}</small></span></div>")
    return "".join(out)


SPLIT_KEY = ("<p class='key'><i class='k-bad'></i>costly (lost 5%+ win chance) <i class='k-ok'></i>fine"
             " &middot; each bar = all moves in that group</p>")


def _strike_card(st: dict) -> str:
    if not st["games"]:
        return "<p class='cap'>No games with a first strike yet.</p>"
    rows = [("You struck first", st["you"]), ("They struck first", st["them"]), ("No strike", st["none"])]
    out = []
    for label, r in rows:
        if not r["n"]:
            continue
        out.append(f"<div class='sb' title='{_e(label)}: {r['n']} games, you won {r['won']}'><span class='lab'>{_e(label)}</span>"
                   f"<span class='trk'><span class='won' style='width:{r['won'] / st['games'] * 100:.1f}%'></span>"
                   f"<span class='lost' style='width:{(r['n'] - r['won']) / st['games'] * 100:.1f}%'></span></span>"
                   f"<span class='val'><b>{r['n']}</b> games <small>won {r['won']}</small></span></div>")
    notes = []
    blunders = [f"{st['blunder_you']} by a blunder of yours" if st["blunder_you"] else "",
                f"{st['blunder_them']} by a blunder of theirs" if st["blunder_them"] else ""]
    if any(blunders):
        notes.append("no strike: " + " and ".join(b for b in blunders if b) + " decided the game")
    if st["door_you"]:
        notes.append(f"of the strikes, {st['door_you']} came right after a move of yours that let it in")
    if st["door_them"]:
        notes.append(f"{st['door_them']} {'was' if st['door_them'] == 1 else 'were'} you punishing their mistake")
    key = ("<p class='key'><i style='background:var(--s1)'></i>won <i style='background:var(--s2);margin-left:6px'></i>"
           "not won &middot; bar length = share of all games</p>")
    return key + "".join(out) + (f"<p class='cap' style='margin-top:8px'>{'; '.join(notes).capitalize()}.</p>" if notes else "")


def _per_game_line(games: list[dict]) -> str:
    """WP lost per move in each game, oldest to newest (orange dots = games you lost)."""
    if len(games) < 2:
        return "<p class='cap'>Needs two games or more.</p>"
    W, H, L, R, T, B = 420, 150, 34, 10, 10, 24
    top = max(g["per_move"] for g in games) * 1.15 or 0.01
    x = lambda i: L + i / (len(games) - 1) * (W - L - R)  # noqa: E731
    y = lambda v: T + (1 - v / top) * (H - T - B)  # noqa: E731
    parts = [f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y(top / 2):.1f}' y2='{y(top / 2):.1f}'/>",
             f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y(0):.1f}' y2='{y(0):.1f}'/>",
             f"<text class='ax' x='{L - 4}' y='{y(0) + 3:.1f}' text-anchor='end'>best</text>",
             "<polyline class='ln' points='" + " ".join(f"{x(i):.1f},{y(g['per_move']):.1f}" for i, g in enumerate(games)) + "'/>"]
    for i, g in enumerate(games):
        tip = (f"{g['id']} ({g['date']}, {g['color']}, {g['result']}): average move {went(g['before'], g['after'])}, "
               f"{g['costly']} costly moves" + (f", under 30s from move {g['first_tt']}" if g["first_tt"] else ""))
        parts.append(f"<a href='{_e(game_url(g['id']))}'><circle class='dot{'' if g['won'] else ' lost'}' cx='{x(i):.1f}' "
                     f"cy='{y(g['per_move']):.1f}' r='5'><title>{_e(tip)}</title></circle></a>")
        parts.append(f"<text class='ax' x='{x(i):.1f}' y='{H - 6}' text-anchor='middle'>{i + 1}</text>")
    return (f"<svg class='pline' viewBox='0 0 {W} {H}' role='img' aria-label='How much your average move cost in each game'>"
            f"{''.join(parts)}</svg>")


def _clock_line(clock: list[tuple[int, float, int]]) -> str:
    """Your clock minus theirs, averaged over games, by move number."""
    if len(clock) < 5:
        return "<p class='cap'>Needs more games with clocks.</p>"
    W, H, L, R, T, B = 420, 150, 40, 10, 10, 24
    lo = min(min(d for _, d, _ in clock), -10)
    hi = max(max(d for _, d, _ in clock), 10)
    n0, n1 = clock[0][0], clock[-1][0]
    x = lambda n: L + (n - n0) / max(1, n1 - n0) * (W - L - R)  # noqa: E731
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)  # noqa: E731
    parts = [f"<line class='zero' x1='{L}' x2='{W - R}' y1='{y(0):.1f}' y2='{y(0):.1f}'/>",
             f"<text class='ax' x='{L - 4}' y='{y(0) + 3:.1f}' text-anchor='end'>even</text>",
             f"<text class='ax' x='{L - 4}' y='{y(hi) + 8:.1f}' text-anchor='end'>+{hi:.0f}s</text>",
             f"<text class='ax' x='{L - 4}' y='{y(lo):.1f}' text-anchor='end'>{lo:.0f}s</text>",
             "<polyline class='ln' points='" + " ".join(f"{x(n):.1f},{y(d):.1f}" for n, d, _ in clock) + "'>"
             "</polyline>"]
    for n, d, k in clock:
        parts.append(f"<circle cx='{x(n):.1f}' cy='{y(d):.1f}' r='7' fill='transparent'>"
                     f"<title>Move {n}: you {'+' if d >= 0 else ''}{d:.0f}s against your opponent (average of {k} games)</title></circle>")
    step = 10
    for n in range((n0 // step + 1) * step, n1 + 1, step):
        parts.append(f"<text class='ax' x='{x(n):.1f}' y='{H - 6}' text-anchor='middle'>{n}</text>")
    return (f"<svg class='pline' viewBox='0 0 {W} {H}' role='img' aria-label='Your clock minus your opponents by move'>"
            f"{''.join(parts)}</svg>")


def render(p: dict) -> str:
    """The 'Patterns across your games' section of the home page."""
    if len(p["games"]) < 2:
        return ""
    strong, weak = findings(p)
    games = p["games"]
    wins = sum(g["won"] for g in games)

    def col(cls, icon, title, items):
        body = "".join(
            f"<p><b>{_e(f['head'])}{'<span class=early>early signal</span>' if f['early'] else ''}</b>{_e(f['body'])}"
            f" <span class='n'>({f['n']} {f.get('unit', 'moves')}"
            + (f" · {bayes.verdict(f['sure'])}" if f.get("sure") is not None else "") + ")</span></p>" for f in items) or "<p class='n'>Nothing clear yet.</p>"
        return f"<div class='fcol {cls}'><h3><i>{icon}</i>{title}</h3>{body}</div>"

    from .gaps import GAP_LABEL

    gap_total = sum(v for _, v in p["gaps"]) or 1
    gaps = _hbars([(GAP_LABEL.get(k, k), v / gap_total, _pct(v / gap_total),
                    f"{GAP_LABEL.get(k, k)}: {_pct(v / gap_total)} of the win % lost in costly moves")
                   for k, v in p["gaps"]], scale=max(v for _, v in p["gaps"]) / gap_total if p["gaps"] else 1,
                  muted={GAP_LABEL["time_trouble"]})
    pieces = _rate_bars([(k, v) for k, v in p["piece"] if v["n"] >= 5 and k != "castling"])
    kpis = [
        (f"{len(games)}", f"games ({wins} won)"),
        (f"{p['moves']}", "moves reviewed"),
        (went(statistics.mean(g['before'] for g in games), statistics.mean(g['after'] for g in games)), "your average move"),
        (f"{statistics.mean(g['costly'] for g in games):.1f}", "costly moves per game"),
        (_pct(p["tt"]["share"]), "of all loss in time trouble"),
    ]
    return f"""<section class="profile">
<h2>Patterns across your games</h2>
<p class="sub">Things one game is too small to show. Each line says how many moves it rests on; under {SOLID} it's an early signal.</p>
<div class="pkpis">{''.join(f"<div class='pkpi'><b>{v}</b><span>{_e(t)}</span></div>" for v, t in kpis)}</div>
<div class="findings">{col('good', '&#10003;', 'Strong points', strong)}{col('weak', '!', 'Weak points', weak)}</div>
{questions.render(p["aagaard"])}
<div class="pgrid">
  <div class="pcard"><h3>Costly moves by think time</h3><p class="cap">Time-trouble moves left out</p>{SPLIT_KEY}{_rate_bars(p['think'])}</div>
  <div class="pcard"><h3>Your candidate moves</h3><p class="cap">Candidates = moves you drew an arrow for or said, before playing</p>{SPLIT_KEY}
    <h4>How many you considered</h4>{_rate_bars(p['cands'], {"none": "None", "one": "1 move", "two or more": "2 or more"})}
    <h4>Was a good move among them?</h4>{_rate_bars(p['good'], {"yes": "Yes", "no": "No"})}</div>
  <div class="pcard"><h3>Your average move, by phase</h3><p class="cap">Where your win chance went on an average move; shorter bar = it cost less</p>{_hbars([(n, r['loss'], f"{went(r['before'], r['after'])} <small>of {r['n']}</small>", f"{n}: {r['n']} moves, {r['costly']} costly") for n, r in p['phase'] if r['n']])}</div>
  <div class="pcard"><h3>Your clock against theirs</h3><p class="cap">Seconds ahead (+) or behind (−), averaged by move number</p>{_clock_line(p['clock'])}</div>
  <div class="pcard"><h3>Each game</h3><p class="cap">How much your average move cost, oldest to newest (lower is better); orange = a game you lost. Click a dot.</p>{_per_game_line(games)}</div>
  <div class="pcard"><h3>What went wrong, by cause, all games</h3><p class="cap">Share of what your costly moves cost you, by cause</p>{gaps}</div>
  <div class="pcard"><h3>Who struck first</h3><p class="cap">The first tactic in each game, and how those games ended</p>{_strike_card(p['strikes'])}</div>
  <div class="pcard"><h3>Your voice and your play</h3><p class="cap">What you sounded like before each move, and how
    often those moves went wrong (time trouble left out)</p>{SPLIT_KEY}{_tone_card(p['tone'])}</div>
  <div class="pcard"><h3>Costly moves by piece</h3><p class="cap">Which piece you moved (5+ moves)</p>{SPLIT_KEY}{pieces}</div>
</div>
</section>"""
