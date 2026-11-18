"""Milestone 4: compare what I drew with the engine, per move, and attribute mistakes to gaps.

Inputs, joined by ply:
  - engine analysis of my moves (Milestone 1, analyse.py)
  - thinking windows with the shapes I drew (Milestone 3, align.py)

Q3 (opponent's idea): the engine's null-move threat. It counts as a real threat when its swing is
    at least `threat_swing`. It was spotted if I drew (or said) the same move. An undrawn threat
    counts as handled when my move kept me clear of what ignoring it would cost (by more than
    `margin`), e.g. taking the attacking piece. Whether that move was the best is Q4's question.
Q4 (candidates): the engine's candidates (MultiPV within the margin of the best) against the moves
    I drew or played:
      found             engine candidate that I drew or played
      wrongly rejected  engine candidate I drew, but I played a move outside the margin
      missed            engine candidate I neither drew nor played
      drawn, not good   a move I drew that is not an engine candidate (evaluated if an engine is given)

A costly move (WP loss >= `mistake`) is attributed to the first gap that explains it:
  0. time trouble     under `time_trouble` seconds on my clock: no time to draw or talk, so the
                      drawings say nothing about my thinking
  1. opponent's idea  a real threat I didn't draw, and my move did about as badly as ignoring it
  2. wrongly rejected I drew a good move and played a worse one
  3. missed candidate none of the engine's good moves were drawn
  4. no thoughts      I drew nothing for this move, so there is no evidence either way
  5. other            something the four questions don't capture yet (calculation, Q1/Q2 later)

The engine decides what is good; the drawings only say what I considered. An undrawn move is
"not drawn", which is not proof that I never thought of it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import chess

from .engine import win_prob
from .fmt import pct, spct

GAPS = ["time_trouble", "opponent_idea", "wrongly_rejected", "missed_candidate", "no_thoughts", "other"]
GAP_LABEL = {
    "time_trouble": "time trouble",
    "opponent_idea": "opponent's idea not seen",
    "wrongly_rejected": "good move considered, then rejected",
    "missed_candidate": "good moves not considered",
    "no_thoughts": "no candidates shown",
    "other": "other",
}


@dataclass
class Thresholds:
    mistake: float = 0.05  # WP loss that counts as a costly move (small drifts add up)
    threat_swing: float = 0.10  # null-move swing that counts as a real threat
    time_trouble: float = 30.0  # seconds on my clock below which a costly move is put down to the clock
    margin: float = 0.05  # candidate margin, as in the engine analysis


def _drawn(window: dict | None) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """Distinct drawn candidate moves and opponent ideas (keyed by uci/squares), and key squares."""
    candidates: dict[str, dict] = {}
    ideas: dict[str, dict] = {}
    squares: list[str] = []
    for s in (window or {}).get("shapes", []):
        source = s.get("source", "arrow")
        # Resolved moves are keyed by their squares, so an arrow and a spoken mention of the same move
        # count once. Unresolved speech ("knight takes", ambiguous moves) is kept by what was said.
        key = f"{s['from']}{s['to']}" if s["from"] and s["to"] else f"said:{s.get('said')}"
        label = s["move_san"] or (f"{s['from']}-{s['to']}" if s["from"] and s["to"] else
                                  "/".join(s["ambiguous"]) + "?" if s.get("ambiguous") else f"\"{s.get('said')}\"")
        if s["kind"] == "candidate":
            entry = candidates.setdefault(
                key, {"from": s["from"], "to": s["to"], "san": label, "legal": bool(s["legal"]), "times": 0,
                      "first_drawn_s": s["drawn_s"], "phase": s["phase"], "sources": set()}
            )
            entry["times"] += 1
            entry["sources"].add(source)
        elif s["kind"] == "opponent_idea":
            entry = ideas.setdefault(key, {"from": s["from"], "to": s["to"], "san": label, "times": 0, "sources": set()})
            entry["times"] += 1
            entry["sources"].add(source)
        elif s["kind"] == "key_square" and s["from"] and s["from"] not in squares:
            squares.append(s["from"])
    return candidates, ideas, squares


def _how(sources: set) -> str:
    """'arrow', 'said' or 'arrow+said'."""
    return "+".join({"drawn": "arrow"}.get(x, x) for x in ("drawn", "said") if {"drawn": "arrow", "said": "voice"}[x] in sources)


def compare_move(record: dict, window: dict | None, t: Thresholds, evaluate=None) -> dict:
    """Gap analysis of one move. `record` is a Milestone 1 move record, `window` a Milestone 3 window.

    `evaluate(fen, uci) -> wp` (optional) scores drawn moves that are not engine candidates.
    """
    drawn_cands, drawn_ideas, key_squares = _drawn(window)
    played = record["move_uci"]
    engine_cands = record["candidates"]
    engine_ucis = [c["move_uci"] for c in engine_cands]
    considered = set(k for k, v in drawn_cands.items() if v["legal"]) | {played}

    drawn_list = []
    for uci, d in drawn_cands.items():
        item = {
            "move_san": d["san"],
            "move_uci": uci if d["legal"] else None,
            "times_drawn": d["times"],
            "first_drawn_s": d["first_drawn_s"],
            "phase": d["phase"],
            "legal": d["legal"],
            "played": uci == played,
            "engine_candidate": uci in engine_ucis,
            "how": _how(d["sources"]),
            "wp": None,
            "loss": None,
        }
        if not d["legal"]:
            pass  # not a move in this position (or speech that couldn't be pinned to one move)
        elif uci in engine_ucis:
            item["wp"] = next(c["wp"] for c in engine_cands if c["move_uci"] == uci)
        elif uci == played:
            item["wp"] = record["wp_after"]
        elif evaluate is not None:
            item["wp"] = round(evaluate(record["fen"], uci), 3)
        if item["wp"] is not None:
            item["loss"] = round(max(0.0, record["wp_before"] - item["wp"]), 3)
        drawn_list.append(item)

    # Q4. A drawn move outside the engine's list that scores within the margin of the best is good too.
    best_wp = record["wp_before"]
    for item in drawn_list:
        if not item["engine_candidate"] and item["wp"] is not None and best_wp - item["wp"] <= t.margin:
            item["engine_candidate"] = True
            item["extra_good"] = True
    extra = [d for d in drawn_list if d.get("extra_good")]
    found = [c["move_san"] for c in engine_cands if c["move_uci"] in considered] + [d["move_san"] for d in extra]
    missed = [c["move_san"] for c in engine_cands if c["move_uci"] not in considered]
    bad_move = record["wp_loss"] > t.margin
    wrongly_rejected = [
        c["move_san"] for c in engine_cands if c["move_uci"] in drawn_cands and c["move_uci"] != played and bad_move
    ] + [d["move_san"] for d in extra if not d["played"] and bad_move]
    drawn_not_good = [d["move_san"] for d in drawn_list if d["legal"] and not d["engine_candidate"]]

    # Q3
    threat = record.get("threat")
    if threat is None:
        q3 = {"status": "in_check" if record.get("threat_skipped") else "none", "threat_san": None, "swing": None}
    else:
        real = threat["swing"] >= t.threat_swing
        seen = threat["move_uci"] in drawn_ideas
        # My move dealt with it anyway (e.g. a recapture): no WP lost and clear of what ignoring it costs.
        handled = record["wp_after"] > threat["wp_if_ignored"] + t.margin
        status = "spotted" if seen else ("handled" if handled else "not_drawn")
        q3 = {
            "status": status if real else ("spotted_minor" if seen else "minor"),
            "threat_san": threat["move_san"],
            "swing": threat["swing"],
            "wp_if_ignored": threat["wp_if_ignored"],
        }

    # Pieces I picked up (clicked or started dragging), and whether I moved that piece in the end.
    best_uci = next((c["move_uci"] for c in engine_cands if c["move_san"] == record["best_move_san"]), None)
    picked_up: dict[str, dict] = {}
    for s in (window or {}).get("shapes", []):
        if s["kind"] == "picked_up" and s["from"] not in picked_up:
            picked_up[s["from"]] = {
                "square": s["from"],
                "piece": s["piece"],
                "moved": bool(s["played"]),
                "best_piece": bool(best_uci) and best_uci[:2] == s["from"],
                "held_s": s["on_board_s"],
            }

    # Attribution
    mistake = record["wp_loss"] >= t.mistake
    gap = None
    if mistake:
        has_thoughts = bool(drawn_cands or drawn_ideas or key_squares or picked_up)
        ignored_threat = (
            q3["status"] == "not_drawn" and record["wp_after"] <= q3["wp_if_ignored"] + t.margin
        )
        clock = record.get("clock_before")
        if clock is not None and clock < t.time_trouble:
            gap = "time_trouble"
        elif ignored_threat:
            gap = "opponent_idea"
        elif wrongly_rejected:
            gap = "wrongly_rejected"
        elif not has_thoughts:
            gap = "no_thoughts"
        elif not found:
            gap = "missed_candidate"
        else:
            gap = "other"

    return {
        "ply": record["ply"],
        "move_number": record["move_number"],
        "move_san": record["move_san"],
        "fen": record["fen"],
        "wp_before": record["wp_before"],
        "wp_after": record["wp_after"],
        "wp_loss": record["wp_loss"],
        "best_move_san": record["best_move_san"],
        "own_time_s": window["own_time_s"] if window else None,
        "opponent_time_s": window["opponent_time_s"] if window else None,
        "time_spent_pgn": window["time_spent_pgn"] if window else record.get("time_spent"),
        "clock_before": record.get("clock_before"),
        "drawn_candidates": drawn_list,
        "drawn_opponent_ideas": [
            v["san"] + ("" if _how(v["sources"]) == "arrow" else f" ({_how(v['sources'])})")
            for v in drawn_ideas.values()
        ],
        "worst_pieces": [
            s["from"] or s.get("said") for s in (window or {}).get("shapes", []) if s["kind"] == "worst_piece"
        ],
        "key_squares": key_squares,
        "picked_up": list(picked_up.values()),
        "q4": {
            "engine": [c["move_san"] for c in engine_cands],
            "found": found,
            "wrongly_rejected": wrongly_rejected,
            "missed": missed,
            "drawn_not_good": drawn_not_good,
        },
        "q3": q3,
        "mistake": mistake,
        "gap": gap,
    }


def build(analysis: dict, thinking: dict, t: Thresholds | None = None, evaluate=None) -> dict:
    """Gap report for one game from the Milestone 1 and Milestone 3 results."""
    t = t or Thresholds(margin=analysis["settings"]["candidate_margin"])
    windows = {w["ply"]: w for w in thinking["windows"]}
    moves, skipped = [], {}
    for record in analysis["moves"]:
        if record["skipped"]:
            skipped[record["skipped"]] = skipped.get(record["skipped"], 0) + 1
            continue
        moves.append(compare_move(record, windows.get(record["ply"]), t, evaluate))

    by_gap = {g: {"count": 0, "wp_lost": 0.0} for g in GAPS}
    for m in moves:
        if m["gap"]:
            by_gap[m["gap"]]["count"] += 1
            by_gap[m["gap"]]["wp_lost"] = round(by_gap[m["gap"]]["wp_lost"] + m["wp_loss"], 3)

    with_thoughts = [m for m in moves if m["drawn_candidates"] or m["drawn_opponent_ideas"] or m["key_squares"]]
    in_time_trouble = lambda m: m["clock_before"] is not None and m["clock_before"] < t.time_trouble  # noqa: E731
    # Threats while in time trouble say nothing about thinking either, so they aren't counted.
    real_threats = [m for m in moves if m["q3"]["status"] in ("spotted", "handled", "not_drawn")
                    and not in_time_trouble(m)]
    summary = {
        "moves_reviewed": len(moves),
        "skipped": skipped,
        "moves_with_thoughts": len(with_thoughts),
        "mistakes": sum(1 for m in moves if m["mistake"]),
        "wp_lost_total": round(sum(m["wp_loss"] for m in moves), 3),
        "by_gap": by_gap,
        "q3_real_threats": len(real_threats),
        "q3_spotted": sum(1 for m in real_threats if m["q3"]["status"] == "spotted"),
        "q3_handled": sum(1 for m in real_threats if m["q3"]["status"] == "handled"),
        "q3_in_time_trouble": sum(1 for m in moves if m["q3"]["status"] in ("spotted", "handled", "not_drawn")
                                  and in_time_trouble(m)),
        # Q4 rates only where I drew something: otherwise "not drawn" says nothing.
        # Picked up the piece the engine's best move needed, then moved a different one.
        "picked_best_piece_not_moved": sum(
            1 for m in moves if any(p["best_piece"] and not p["moved"] for p in m.get("picked_up", []))
        ),
        "q4_best_considered": sum(1 for m in with_thoughts if m["best_move_san"] in m["q4"]["found"]),
        "q4_positions": len(with_thoughts),
    }
    return {
        "game": analysis["game"],
        "thresholds": asdict(t),
        "summary": summary,
        "moves": moves,
    }


def engine_evaluator(engine, me: chess.Color, wp_model: str):
    """`evaluate(fen, uci) -> wp` using a Milestone 1 Engine (search restricted to that move)."""

    def evaluate(fen: str, uci: str) -> float:
        board = chess.Board(fen)
        info = engine.analyse(board, multipv=1, root_moves=[chess.Move.from_uci(uci)])[0]
        return win_prob(info, me, wp_model)

    return evaluate


# ---------------------------------------------------------------- report


def render(report: dict) -> str:
    g, s, t = report["game"], report["summary"], report["thresholds"]
    dots = "." if g["my_color"] == "white" else "..."
    label = lambda m: f"{m['move_number']}{dots} {m['move_san']}"  # noqa: E731
    out = [
        "THOUGHT COACH - gap report",
        f"Game {g['id']}: {g['white']} vs {g['black']}  {g['result']}  TC {g['time_control'] or '?'}"
        f"   you: {g['my_color']}",
        f"Costly move = WP loss >= {pct(t['mistake'])}.  Real threat = null-move swing >= {pct(t['threat_swing'])}."
        f"  Candidates = engine lines within {pct(t['margin'])} of the best.",
        f"Time trouble = under {t.get('time_trouble', 30):.0f}s on your clock (costly moves there aren't put down to your thinking).",
        "Considered = you drew an arrow for it or said it aloud. 'Not considered' only means neither, not that you never thought of it.",
        "",
        f"Moves reviewed: {s['moves_reviewed']}"
        + (f" (skipped: {', '.join(f'{n} {k}' for k, n in s['skipped'].items())})" if s["skipped"] else "")
        + f", with thoughts shown: {s['moves_with_thoughts']}",
        f"Total WP lost (summed over your moves): {pct(s['wp_lost_total'])}, of which "
        f"{pct(sum(b['wp_lost'] for b in s['by_gap'].values()))} in {s['mistakes']} costly move(s)",
        "",
        "WHERE THE COSTLY MOVES CAME FROM (WP lost)",
    ]
    v = report.get("verdict") or {}
    if v.get("good") or v.get("watch"):
        short = ["IN SHORT"]
        if v.get("good"):
            short.append(f"  Went well    : {v['good']['text']}")
        if v.get("watch"):
            short.append(f"  Watch out for: {v['watch']['text']}")
        if report.get("strike"):
            short.append(f"  First strike : {report['strike']['text']}")
        out[-1:-1] = short + [""]
    for gap in GAPS:
        b = s["by_gap"][gap]
        out.append(f"  {GAP_LABEL[gap]:<36} {pct(b['wp_lost']):>5}  ({b['count']} move{'s' if b['count'] != 1 else ''})")
    out.append("")
    if "q1_weaknesses" in s:
        out.append(f"Q1 weaknesses: {s['q1_weaknesses']} the engine went for, you pointed at {s['q1_found']}"
                   + (": " + ", ".join(f"{w['square']} ({w['side']} {w['kind']}"
                                        f"{', created by ' + w['created_by'] if w.get('created_by') else ''}{', seen' if w['you'] else ''})"
                                        for w in s["q1_list"]) if s["q1_list"] else ""))
        out.append(f"Q2 worst piece: {s['q2_positions']} passive piece(s), you named or moved {s['q2_found']}"
                   + (": " + ", ".join(f"{w['piece']} {w['square']} (move {w['move']}, {pct(w['activity'])} free"
                                        f"{', seen' if w['you'] else ''})" for w in s["q2_list"]) if s["q2_list"] else ""))
    out.append(
        f"Q3 opponent's idea: {s['q3_real_threats']} real threat(s): {s['q3_spotted']} spotted, "
        f"{s.get('q3_handled', 0)} handled by your move, "
        f"{s['q3_real_threats'] - s['q3_spotted'] - s.get('q3_handled', 0)} not stopped"
        + (f" (+{s['q3_in_time_trouble']} in time trouble, not counted)" if s.get("q3_in_time_trouble") else "")
    )
    out.append(
        f"Q4 candidates: engine's best move considered or played in {s['q4_best_considered']} of "
        f"{s['q4_positions']} moves where you considered something"
    )
    out.append("")

    if report.get("insights"):
        out.append("FROM YOUR THOUGHTS")
        out += [f"  {i['head']}: {i['body']}" for i in report["insights"]]
        out.append("")

    mistakes = [m for m in report["moves"] if m["mistake"]]
    if mistakes:
        out.append("COSTLY MOVES")
        for m in sorted(mistakes, key=lambda m: -m["wp_loss"]):
            why = GAP_LABEL[m["gap"]]
            if m["gap"] == "opponent_idea":
                why += f": {m['q3']['threat_san']} (swing {spct(m['q3']['swing'])})"
            elif m["gap"] == "wrongly_rejected":
                why += f": {', '.join(m['q4']['wrongly_rejected'])}"
            elif m["gap"] == "missed_candidate":
                why += f": {', '.join(m['q4']['missed'])}"
            out.append(f"  {label(m):<14} {spct(-m['wp_loss']):>5}  best {m['best_move_san']:<7} -> {why}")
        out.append("")

    out.append("MOVE BY MOVE")
    for m in report["moves"]:
        flag = f"   <-- {GAP_LABEL[m['gap']]}" if m["gap"] else ""
        time = m["time_spent_pgn"]
        out.append(
            f"  {label(m):<14} WP {pct(m['wp_before']):>4} -> {pct(m['wp_after']):>4} ({spct(-m['wp_loss'])})"
            f"   {'' if time is None else f'{time:.0f}s'}{flag}"
        )
        if m["drawn_candidates"]:
            parts = []
            for d in m["drawn_candidates"]:
                score = "" if d["loss"] is None else f" {spct(-d['loss'])}"
                tag = "*" if d["played"] else ""
                ok = "" if d["legal"] or d["move_san"].endswith("?") else " (not a move here)"
                how = "" if d.get("how", "arrow") in ("arrow", "drawn") else f" [{d['how']}]"
                parts.append(f"{d['move_san']}{tag}{score}{ok}{how}")
            out.append(f"      you had  : {' | '.join(parts)}")
        if m["drawn_opponent_ideas"]:
            out.append(f"      opp. idea: {', '.join(m['drawn_opponent_ideas'])}")
        if m["key_squares"]:
            out.append(f"      squares  : {', '.join(m['key_squares'])}")
        if m.get("picked_up"):
            out.append("      picked up: " + ", ".join(
                f"{p['piece']}{p['square']} ({'moved' if p['moved'] else 'not moved'}"
                + (", the best move's piece" if p["best_piece"] and not p["moved"] else "") + ")"
                for p in m["picked_up"]))
        if m.get("worst_pieces"):
            out.append(f"      worst pc.: {', '.join(m['worst_pieces'])} (said)")
        if m.get("q1", {}).get("weaknesses"):
            out.append("      Q1 weak  : " + ", ".join(
                f"{w['square']} {w['side']} {w['kind']}{' (created by ' + w['created_by'] + ')' if w.get('created_by') else ''}"
                f"{' [' + w['you'] + ']' if w['you'] else ''}"
                for w in m["q1"]["weaknesses"]))
        worst = (m.get("q2") or {}).get("worst")
        if worst:
            plan = f", engine plays {worst['engine_move']}" if worst.get("engine_move") else ", engine leaves it"
            out.append(f"      Q2 worst : {worst['piece']} {worst['square']} ({pct(worst['activity'])} free{plan})"
                       + (f" [{worst['you']}]" if worst.get("you") else ""))
        q4 = m["q4"]
        engine = " | ".join(
            f"{san}{' ok' if san in q4['found'] else ''}" for san in q4["engine"]
        )
        out.append(f"      engine   : {engine}")
        q3 = m["q3"]
        if q3["threat_san"]:
            state = {"spotted": "spotted", "handled": "not spotted, your move dealt with it", "not_drawn": "NOT spotted, and not stopped", "minor": "minor", "spotted_minor": "minor, spotted", "unstoppable": "can't be stopped: they take it in your best line too"}
            out.append(f"      threat   : {q3['threat_san']} (swing {spct(q3['swing'])}, {state[q3['status']]})")
    out.append("")
    out.append("* = played.  ok = an engine candidate you considered or played.  -x% = win chance that move gives up (percentage points).")
    out.append("[said] = from your voice only, [arrow+said] = both; no tag = arrow only.")
    return "\n".join(out)
