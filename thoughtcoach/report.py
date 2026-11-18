"""Readable plain-text report from the analysis JSON."""

from __future__ import annotations
from .fmt import pct, spct

WP_MODEL_NAMES = {
    "lichess": "lichess win% curve (fitted to human games)",
    "sf-wdl": "Stockfish WDL expectation (engine-calibrated)",
}


def fmt_clock(seconds: float | None) -> str:
    if seconds is None:
        return "  -  "
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def move_label(rec: dict, my_color: str) -> str:
    dots = "." if my_color == "white" else "..."
    return f"{rec['move_number']}{dots} {rec['move_san']}"


def render(data: dict, top_n: int = 5) -> str:
    g, s, moves = data["game"], data["settings"], data["moves"]
    color = g["my_color"]
    out = [
        "THOUGHT COACH - engine report",
        f"Game {g['id']}: {g['white']} ({g['white_elo'] or '?'}) vs {g['black']} ({g['black_elo'] or '?'})"
        f"  {g['result']}  {g['date'] or ''}  TC {g['time_control'] or '?'}",
        f"Opening: {g['opening'] or '?'}",
        f"You played {color}. Engine: {s['engine']}, {s['nodes']:,} nodes/search, MultiPV {s['multipv']}, "
        f"{s['threads']} thread(s).",
        f"Win probabilities (WP) are yours, 0-1, using the "
        f"{WP_MODEL_NAMES.get(s.get('wp_model', 'sf-wdl'), s.get('wp_model'))}.",
        "",
    ]

    analysed = [m for m in moves if not m["skipped"]]
    skipped = {}
    for m in moves:
        if m["skipped"]:
            skipped[m["skipped"]] = skipped.get(m["skipped"], 0) + 1
    skip_text = ", ".join(f"{n} {why}" for why, n in skipped.items()) or "none"
    out.append(f"Moves analysed: {len(analysed)} of {len(moves)} (skipped: {skip_text})")
    out.append("")

    worst = sorted(analysed, key=lambda m: m["wp_loss"], reverse=True)[:top_n]
    worst = [m for m in worst if m["wp_loss"] > 0]
    if worst:
        out.append("BIGGEST WP LOSSES")
        for m in worst:
            out.append(
                f"  {move_label(m, color):<14} {spct(-m['wp_loss']):>5}   best {m['best_move_san']:<7}"
                f"  thought {fmt_clock(m['time_spent'])}"
            )
        out.append("")

    out.append("MOVE BY MOVE")
    for m in moves:
        label = move_label(m, color)
        if m["skipped"]:
            extra = ""
            if m.get("wp_after") is not None:
                extra = f"  (WP {pct(m['wp_before'])} -> {pct(m['wp_after'])})"
            out.append(f"  {label:<14} [skipped: {m['skipped']}]{extra}")
            continue
        flag = "" if m["played_within_margin"] else "   <-- outside candidate margin"
        out.append(
            f"  {label:<14} WP {pct(m['wp_before']):>4} -> {pct(m['wp_after']):>4}  ({spct(-m['wp_loss'])})"
            f"   thought {fmt_clock(m['time_spent'])}   clock {fmt_clock(m['clock_after'])}{flag}"
        )
        cands = " | ".join(
            f"{c['move_san']}{'*' if c['move_uci'] == m['move_uci'] else ''} {pct(c['wp'])}"
            for c in m["candidates"]
        )
        more = " (+maybe more)" if m["candidates_may_be_truncated"] else ""
        out.append(f"      candidates : {cands}{more}")
        if m["threat"]:
            t = m["threat"]
            out.append(
                f"      opp. idea  : {t['move_san']} (swing {spct(t['swing'])}, WP if ignored "
                f"{pct(t['wp_if_ignored'])})  {t['line_san']}"
            )
        else:
            out.append(f"      opp. idea  : - ({m.get('threat_skipped', 'no line')})")
        if not m["played_in_candidates"]:
            out.append(f"      your line  : {m['played_line_san']}")
        out.append(f"      fen        : {m['fen']}")
    out.append("")
    out.append("* = the move you played.  swing = WP you'd lose if you ignored the opponent's idea.")
    return "\n".join(out)
