"""One-screen game dashboard: every observation of the review on a single page, no scrolling.

  header      the game and four headline numbers
  takeaways   the few findings that cost the most, in plain words (all derived from engine numbers)
  left        the critical moments, one at a time (tabs), each played back idea by idea (critical.py)
  right       win probability over the game with costly moves marked and time trouble shaded,
              where the WP went (by gap), and the list of costly moves

Everything is computed from the gap report and the engine analysis; nothing here judges a move.
"""

from __future__ import annotations

import html
import json

import chess

from .critical import CSS as REEL_CSS
from .critical import _last_move, pick, player_html, reel, sharpness
from .gaps import GAP_LABEL, GAPS
from .gametype import classify
from .strike import EXPLAIN as STRIKE_EXPLAIN
from .fmt import avg_went, game_url, move_went, pct
from .fmt import THEME_BUTTON, THEME_HEAD
from . import questions, timing

SHORT = {
    "time_trouble": "time trouble",
    "opponent_idea": "threat not stopped",
    "wrongly_rejected": "good move considered, then rejected",
    "missed_candidate": "good moves not considered",
    "no_thoughts": "no candidates shown",
    "other": "other",
}


def _e(x) -> str:
    return html.escape(str(x))


def _label(m: dict, dots: str) -> str:
    return f"{m['move_number']}{dots} {m['move_san']}"


def _example(m: dict, dots: str) -> str:
    """One costly move in a few words: what was played and what the gap was."""
    g, q4, q3 = m["gap"], m["q4"], m["q3"]
    if g == "wrongly_rejected":
        return f"{_label(m, dots)} instead of {', '.join(q4['wrongly_rejected'])}"
    if g == "opponent_idea":
        return f"{_label(m, dots)} allowed {q3['threat_san']}"
    if g == "missed_candidate":
        return f"{_label(m, dots)} ({m['best_move_san']} not considered)"
    return _label(m, dots)


def takeaways(report: dict, dots: str) -> list[str]:
    """Plain-language findings, costliest first."""
    s, moves = report["summary"], report["moves"]
    costly = [m for m in moves if m["mistake"]]
    out: list[tuple[float, str]] = []
    for gap in GAPS:
        ms = sorted((m for m in costly if m["gap"] == gap), key=lambda m: -m["wp_loss"])
        if not ms:
            continue
        lost = sum(m["wp_loss"] for m in ms)
        ex = "; ".join(f"{_example(m, dots)}, {move_went(m)}" for m in ms[:2])
        n = f"{len(ms)} move{'s' if len(ms) > 1 else ''}"
        if gap == "time_trouble":
            out.append((lost, f"<b>Time trouble</b>: {n} played under {report['thresholds'].get('time_trouble', 30):.0f}s, "
                              f"from move {min(m['move_number'] for m in ms)} on; the worst {_e(_label(ms[0], dots))} "
                              f"<b>{move_went(ms[0])}</b>."))
        else:
            out.append((lost, f"<b>{_e(GAP_LABEL[gap].capitalize())}</b>: {n} &mdash; {_e(ex)}."))
    out.sort(key=lambda x: -x[0])
    picked = [m for m in moves if any(p["best_piece"] and not p["moved"] for p in m.get("picked_up", []))]
    lines = [t for _, t in out[: 2 if picked else 3]]  # four lines at most, to fit the screen
    if picked:
        ex = ", ".join(f"{_label(m, dots)} (best {m['best_move_san']})" for m in picked[:2])
        lines.append(f"<b>Right piece, other move</b>: {len(picked)} time{'s' if len(picked) > 1 else ''} you picked up "
                     f"the piece for the engine's best move, then moved another &mdash; {_e(ex)}.")
    real = s["q3_real_threats"]
    if real:
        stopped = real - s["q3_spotted"] - s.get("q3_handled", 0)
        lines.append(f"<b>Threats</b>: of {real} real ones (with time to think) you spotted {s['q3_spotted']}, "
                     f"your move dealt with {s.get('q3_handled', 0)}, {stopped} not stopped.")
    return lines


# ---------------------------------------------------------------- charts (inline SVG)


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def game_chart(report: dict, analysis: dict, critical_plies: list[int], dots: str) -> str:
    """Three panels on one move axis: your win % (costly moves as dots), both clocks, and how long
    you thought about each move. One crosshair reads all three."""
    records = list(analysis.get("moves", []))
    moves = report["moves"]
    if not records or not moves:
        return ""
    W, L, R, H = 720, 40, 56, 250
    P = {"wp": (6, 118), "clock": (130, 180), "time": (192, 232)}  # top, bottom of each panel
    n0, n1 = records[0]["move_number"], records[-1]["move_number"]
    span = max(1, n1 - n0)

    def x(n):
        return L + (n - n0) / span * (W - L - R)

    def scale(panel, hi):
        top, bot = P[panel]
        return lambda v: bot - min(max(v, 0), hi) / (hi or 1) * (bot - top)

    y_wp = scale("wp", 1)
    cmax = max([r.get("clock_before") or 0 for r in records] + [r.get("opp_clock") or 0 for r in records] + [1])
    y_clk = scale("clock", cmax)
    tmax = max([r.get("time_spent") or 0 for r in records] + [1])
    y_t = scale("time", tmax)
    tt = report["thresholds"].get("time_trouble", 30)
    by_ply = {m["ply"]: m for m in moves}
    good = {p["ply"]: p for p in timing.positions(analysis, report)}
    parts = []

    # Time trouble, shaded through all three panels.
    first_tt = next((r["move_number"] for r in records
                     if r.get("clock_before") is not None and r["clock_before"] < tt), None)
    for top, bot in P.values():
        if first_tt is not None:
            x0 = x(first_tt) - 4
            parts.append(f"<rect class='tt' x='{x0:.1f}' y='{top}' width='{W - R - x0:.1f}' height='{bot - top}'/>")
        parts.append(f"<line class='base' x1='{L}' x2='{W - R}' y1='{bot}' y2='{bot}'/>")
    if first_tt is not None:
        parts.append(f"<text class='ttl' x='{W - R - 4}' y='{P['clock'][0] + 10}' text-anchor='end'>time trouble</text>")

    # Panel 1: your win %.
    for wp in (0.25, 0.5, 0.75):
        parts.append(f"<line class='{'mid' if wp == 0.5 else 'grid'}' x1='{L}' x2='{W - R}' y1='{y_wp(wp):.1f}' y2='{y_wp(wp):.1f}'/>"
                     f"<text class='ax' x='{L - 6}' y='{y_wp(wp) + 4:.1f}' text-anchor='end'>{pct(wp)}</text>")
    parts.append(f"<text class='plab' x='{L + 6}' y='{P['wp'][0] + 12}'>Your win %</text>")
    # The line runs to the end of the game: moves skipped as already decided still have a win %.
    first = moves[0]["move_number"]
    line = [(r["move_number"], 1.0 if r["move_san"].endswith("#") else r["wp_after"]) for r in records
            if r.get("wp_after") is not None and r["move_number"] >= first]
    pts = " ".join(f"{x(n):.1f},{y_wp(wp):.1f}" for n, wp in line)
    parts.append(f"<polyline class='wp' points='{pts}'/>")

    # Panel 2: both clocks, with the time-trouble line.
    parts.append(f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y_clk(tt):.1f}' y2='{y_clk(tt):.1f}' stroke-dasharray='3 3'/>"
                 f"<text class='ax' x='{L - 6}' y='{y_clk(tt) + 4:.1f}' text-anchor='end'>{_mmss(tt)}</text>"
                 f"<text class='ax' x='{L - 6}' y='{P['clock'][0] + 8}' text-anchor='end'>{_mmss(cmax)}</text>")
    parts.append(f"<text class='plab' x='{L + 6}' y='{P['clock'][0] + 12}'>Clock</text>")
    ends = []
    for key, cls, name in (("clock_before", "you", "you"), ("opp_clock", "opp", "them")):
        pts = [(x(r["move_number"]), y_clk(r[key])) for r in records if r.get(key) is not None]
        if len(pts) > 1:
            parts.append(f"<polyline class='clk {cls}' points='{' '.join(f'{a:.1f},{b:.1f}' for a, b in pts)}'/>")
            last = next(r for r in reversed(records) if r.get(key) is not None)
            ends.append([pts[-1][1], cls, f"{name} {_mmss(last[key])}"])
    if len(ends) == 2 and abs(ends[0][0] - ends[1][0]) < 11:  # keep the two end labels apart
        lo, hi = sorted(ends, key=lambda e: e[0])
        mid = (lo[0] + hi[0]) / 2
        lo[0], hi[0] = mid - 6, mid + 6
    for yy, cls, text in ends:
        parts.append(f"<text class='endl {cls}' x='{W - R + 4}' y='{yy + 4:.1f}'>{_e(text)}</text>")

    # Panel 3: how long you thought about each move (costly moves dark).
    parts.append(f"<text class='ax' x='{L - 6}' y='{P['time'][0] + 8}' text-anchor='end'>{tmax:.0f}s</text>")
    parts.append(f"<text class='plab' x='{L + 6}' y='{P['time'][0] + 12}'>Think time</text>")
    bw = max(2.0, min(8.0, (W - L - R) / (span + 1) * 0.6))
    for r in records:
        spent = r.get("time_spent")
        if not spent:
            continue
        m = by_ply.get(r["ply"])
        top = y_t(spent)
        cls = "bar-t costly" if m and m["mistake"] else "bar-t"
        parts.append(f"<rect class='{cls}' x='{x(r['move_number']) - bw / 2:.1f}' y='{top:.1f}' width='{bw:.1f}' "
                     f"height='{P['time'][1] - top:.1f}' rx='1.5'/>")
    for p in good.values():  # positions where only one move held: a mark under the bar
        if p["critical"]:
            parts.append(f"<path class='crit' d='M{x(p['move_number']):.1f},{P['time'][1] + 3:.1f} l4,6 l-8,0 z'/>")

    # The first strike: where the game broke open into tactics (strike.py).
    st = report.get("strike") or {}
    if st.get("side"):
        sx = x(min(max(st["move_number"], n0), n1))
        who = "you" if st["side"] == "you" else "them"
        parts.append(f"<g class='strike {who}'><title>{_e(st['text'])}</title>"
                     f"<line x1='{sx:.1f}' x2='{sx:.1f}' y1='{P['wp'][0] + 14}' y2='{P['wp'][1]}'/>"
                     f"<text x='{sx + (3 if sx < W - R - 90 else -3):.1f}' y='{P['wp'][0] + 24}' "
                     f"text-anchor='{'start' if sx < W - R - 90 else 'end'}'>&#9889; {'you' if who == 'you' else 'they'} struck</text></g>")

    # Move numbers.
    step = 5 if span <= 40 else 10
    for n in range((n0 // step + 1) * step, n1 + 1, step):
        parts.append(f"<text class='ax' x='{x(n):.1f}' y='{H - 4}' text-anchor='middle'>{n}</text>")

    # Costly moves on the win % line; numbers are the critical moments (click to open).
    for m in moves:
        if not m["mistake"]:
            continue
        cx, cy = x(m["move_number"]), y_wp(m["wp_after"])
        num = critical_plies.index(m["ply"]) + 1 if m["ply"] in critical_plies else None
        cls = "dot tt-dot" if m["gap"] == "time_trouble" else "dot"
        r = 4 + min(6, m["wp_loss"] * 25)
        if num and m["wp_loss"] > 0.15:  # a long drop line would run through a label above the dot
            label = f"<text class='num' x='{cx - r - 4:.1f}' y='{cy + 4:.1f}' text-anchor='end'>{num}</text>"
        elif num:
            label = f"<text class='num' x='{cx:.1f}' y='{cy - r - 4:.1f}' text-anchor='middle'>{num}</text>"
        else:
            label = ""
        parts.append(f"<g class='pt'{f' data-moment={num - 1}' if num else ''}>"
                     f"<line class='drop' x1='{cx:.1f}' x2='{cx:.1f}' y1='{y_wp(m['wp_before']):.1f}' y2='{cy:.1f}'/>"
                     f"<circle class='{cls}' cx='{cx:.1f}' cy='{cy:.1f}' r='{r:.1f}'/>"
                     f"<circle class='hit' cx='{cx:.1f}' cy='{cy:.1f}' r='12'/>{label}</g>")

    # One readout per move with every panel's value at that move.
    points = []
    for r in records:
        m = by_ply.get(r["ply"])
        rows = []
        if m:
            rows.append([pct(m["wp_after"]), "win % after", "wp"])
            if m["mistake"]:
                rows.append([move_went(m), SHORT.get(m["gap"], "costly"), ""])
            drawn = len([d for d in m.get("drawn_candidates", []) if d.get("legal")])
            rows.append([str(drawn), "candidates considered", ""])
        elif r.get("wp_after") is not None and r.get("skipped") == "decided":
            wp = 1.0 if r["move_san"].endswith("#") else r["wp_after"]
            rows.append([pct(wp), "win % after", "wp"])
            rows.append(["decided", "not reviewed", ""])
        else:
            rows.append([r.get("skipped") or "book", "not reviewed", ""])
        rows.append([_mmss(r.get("clock_before")), "your clock", "you"])
        rows.append([_mmss(r.get("opp_clock")), "their clock", "opp"])
        rows.append([f"{r.get('time_spent') or 0:.0f}s", "think time", ""])
        p = good.get(r["ply"])
        if p and p["critical"]:
            rows.append(["only", f"{p['best']} held (critical)", ""])
        elif p and not p["obvious"]:
            rows.append([str(p["good"]), f"good move{'s' * (p['good'] != 1)}", ""])
        points.append({"x": round(x(r["move_number"]), 1), "label": f"{r['move_number']}{dots} {r['move_san']}", "rows": rows})
    parts.append(f"<line class='xh' x1='0' x2='0' y1='{P['wp'][0]}' y2='{P['time'][1]}' visibility='hidden'/>")
    data = json.dumps(points).replace("</", "<\\/")
    return (f"<svg class='timeline' viewBox='0 0 {W} {H}' preserveAspectRatio='xMidYMid meet' role='img' "
            f"aria-label='Your win percentage, both clocks and your think time for each move'>{''.join(parts)}</svg>"
            f"<script type='application/json' class='chart-data'>{data}</script>")


def time_card(report: dict, analysis: dict) -> str:
    """Time where it mattered (timing.py): your think time where only one move held against the rest,
    the critical positions you rushed, and the long thinks where several moves were fine."""
    t = timing.game(analysis, report)
    if not t:
        return ""
    white = report["game"]["my_color"] == "white"
    hi = max(t["crit_median"], t["other_median"], 1)
    bars = "".join(
        f"<div class='tc-row'><span>{name}</span><span class='tc-track'><span class='tc-bar {cls}' "
        f"style='width:{v / hi * 100:.1f}%'></span></span><b>{v:.0f} s</b></div>"
        for name, cls, v in ((f"Only one move held ({t['critical']})", "crit", t["crit_median"]),
                             (f"Everything else ({t['positions'] - t['critical']})", "", t["other_median"])))
    items = []
    for p in t["rushed"][:3]:
        items.append(f"<li class='rushed'><b>{_e(timing.label(p, white))}</b> {p['time']:.0f} s. Only {_e(p['best'])} held, "
                     f"and your win chance went {pct(p['wp_before'])} → {pct(p['wp_after'])} (your usual think: {t['usual']:.0f} s).</li>")
    for p in t["lingered"][:2]:
        items.append(f"<li class='lingered'><b>{_e(timing.label(p, white))}</b> {p['time']:.0f} s, with {p['good']} moves "
                     f"about equally good.</li>")
    note = (f"<ul class='tc-list'>{''.join(items)}</ul>" if items else
            "<p class='more'>No critical position was rushed this game.</p>")
    return (f"<section class='card timecard'><h3>Time where it mattered</h3><p class='tc-lead'>{_e(timing.sentence(t))}</p>"
            f"<div class='tc-bars'>{bars}</div><p class='more'>Median think time. Recaptures and moves in time trouble "
            f"are left out.</p>{note}</section>")


def gap_bars(report: dict) -> str:
    """WP lost per gap, as horizontal bars (HTML)."""
    by_gap = report["summary"]["by_gap"]
    top = max([b["wp_lost"] for b in by_gap.values()] + [0.01])
    rows, none = [], []
    for gap in [g for g in GAPS if g != "time_trouble"] + ["time_trouble"]:
        b = by_gap.get(gap, {"wp_lost": 0, "count": 0})
        if not b["count"]:
            if gap != "other":
                none.append(SHORT[gap].lower())
            continue
        width = b["wp_lost"] / top * 100
        worst = max((m for m in report["moves"] if m["mistake"] and m["gap"] == gap), key=lambda m: m["wp_loss"], default=None)
        moves = f"{b['count']} move{'s' if b['count'] != 1 else ''}"
        rows.append(f"<div class='bar-row{' muted' if gap == 'time_trouble' else ''}' "
                    f"data-tip='{_e(GAP_LABEL[gap])}: {moves}" + (f", the worst {move_went(worst)}" if worst else "") + "'>"
                    f"<span class='bar-label'>{_e(SHORT[gap])}</span>"
                    f"<span class='bar-val'>{moves}" + (f"<small> &middot; worst {move_went(worst)}</small>" if worst else "") + "</span>"
                    f"<span class='bar-track'><span class='bar' style='width:{width:.1f}%'></span></span></div>")
    if none:
        rows.append(f"<p class='more'>Nothing lost to: {_e(', '.join(none))}.</p>")
    return "".join(rows) or "<p class='more'>No costly moves.</p>"


def verdict_html(report: dict, critical_plies: list[int]) -> str:
    """In short: one thing that went well, one thing to watch out for (see insights.verdict), and
    who struck first (strike.py)."""
    v = report.get("verdict") or {}
    cells = []
    st = report.get("strike")
    for key, icon, title in (("good", "&#10003;", "Went well"), ("watch", "!", "Watch out for")):
        item = v.get(key)
        if not item:
            continue
        ply = item.get("ply")
        attr = f" data-moment='{critical_plies.index(ply)}'" if ply in critical_plies else ""
        cells.append(f"<div class='v-{key}'{attr}><i aria-hidden='true'>{icon}</i><b>{title}</b><p>{_e(item['text'])}</p></div>")
    if st and cells:
        brk = (st.get("break") or {}).get("text")
        more = f"<p class='v-break'>{_e(brk)}</p>" if brk else ""
        cells.append(f"<div class='v-strike' title='{_e(STRIKE_EXPLAIN)}'><i aria-hidden='true'>&#9889;</i><b>First strike</b>"
                     f"<p>{_e(st['text'])}</p>{more}<p class='v-what'>{_e(STRIKE_EXPLAIN)}</p></div>")
    return f"<div class='verdict'>{''.join(cells)}</div>" if cells else ""


def _meter(parts: list[tuple[int, str, str]]) -> str:
    """A thin segmented bar: (count, colour, label) per segment; zero-count segments are left out."""
    total = sum(n for n, _, _ in parts) or 1
    return "<div class='meter'>" + "".join(
        f"<span style='width:{n / total * 100:.1f}%;background:{c}' title='{_e(label)}'></span>"
        for n, c, label in parts if n) + "</div>"


def threat_meter(s: dict) -> str:
    real = s["q3_real_threats"]
    if not real:
        return "<div class='kpi'><b>–</b><span>no real threats with time to think</span></div>"
    drawn, handled = s["q3_spotted"], s.get("q3_handled", 0)
    missed = real - drawn - handled
    bar = _meter([(drawn, "var(--s1)", "spotted"), (handled, "var(--s1-light)", "your move dealt with it"),
                  (missed, "var(--s2)", "not stopped")])
    return (f"<div class='kpi'><b>{drawn + handled}/{real}</b><span>real threats seen or stopped</span>{bar}"
            f"<small>{drawn} spotted · {handled} by your move · {missed} missed</small></div>")


def questions_tile(s: dict) -> str:
    """Aagaard's three questions as a meter list: what the engine found, and how much of it you saw."""
    rows = []

    def row(name: str, tip: str, figure: str, parts) -> None:
        rows.append(f"<div class='q' title='{_e(tip)}'><span class='qn'>{name}</span>{_meter(parts)}<b>{figure}</b></div>")

    if "q1_weaknesses" in s:
        n, f = s["q1_weaknesses"], s["q1_found"]
        row("Weaknesses", f"{n} weaknesses the engine went for; you marked or targeted {f}",
            f"{f}/{n}" if n else "–", [(f, "var(--s1)", "seen"), (n - f, "var(--s1-light)", "not pointed at")])
        n, f = s["q2_positions"], s["q2_found"]
        row("Worst piece", f"{n} passive pieces; you named or moved {f}",
            f"{f}/{n}" if n else "–", [(f, "var(--s1)", "seen"), (n - f, "var(--s1-light)", "not named")])
    real = s["q3_real_threats"]
    drawn, handled = s["q3_spotted"], s.get("q3_handled", 0)
    row("Their idea", f"{real} real threats: {drawn} spotted, {handled} dealt with by your move, "
        f"{real - drawn - handled} missed", f"{drawn}/{real}" if real else "–",
        [(drawn, "var(--s1)", "spotted"), (handled, "var(--s1-light)", "dealt with by your move"),
         (real - drawn - handled, "var(--s2)", "missed")])
    return (f"<a class='kpi qs' href='#aagaard' title='See every question, with positions from this game'>"
            f"<span>Aagaard's three questions &darr;</span>{''.join(rows)}</a>")


def best_meter(s: dict) -> str:
    n, total = s["q4_best_considered"], s["q4_positions"]
    if not total:
        return "<div class='kpi'><b>–</b><span>best move among yours</span></div>"
    bar = _meter([(n, "var(--s1)", "best move among yours"), (total - n, "var(--s1-light)", "not among yours")])
    return (f"<div class='kpi'><b>{n}/{total}</b><span>best move among yours</span>{bar}"
            f"<small>{pct(n / total)} of moves you considered something on</small></div>")


def thoughts_card(report: dict, critical_plies: list[int], limit: int = 4) -> str:
    """'From your thoughts': what you said and drew, set against the engine (see insights.py)."""
    items = report.get("insights") or []
    if not items:
        return ""
    rows = []
    for item in items[:limit]:
        k = critical_plies.index(item["ply"]) if item["ply"] in critical_plies else None
        attr = f" data-moment='{k}'" if k is not None else ""
        rows.append(f"<p{attr}><b>{_e(item['head'])}</b> {_e(item['body'])}</p>")
    return f"<div class='card thoughts'><h3>From your thoughts</h3><div class='ins'>{''.join(rows)}</div></div>"


def costly_list(report: dict, dots: str, critical_plies: list[int], limit: int = 8) -> str:
    costly = sorted((m for m in report["moves"] if m["mistake"]), key=lambda m: m["move_number"])
    rows = []
    for m in costly[:limit]:
        num = critical_plies.index(m["ply"]) + 1 if m["ply"] in critical_plies else None
        badge = f"<span class='badge'>{num}</span>" if num else ""
        extra = ""
        if m["gap"] == "wrongly_rejected":
            extra = f"had {', '.join(m['q4']['wrongly_rejected'])}"
        elif m["gap"] == "opponent_idea":
            extra = f"{m['q3']['threat_san']}"
        elif m["gap"] in ("missed_candidate", "no_thoughts"):
            extra = f"best {m['best_move_san']}"
        rows.append(f"<tr class='{'muted' if m['gap'] == 'time_trouble' else ''}'{f' data-moment={num - 1}' if num else ''}>"
                    f"<td class='mv'>{badge}{_e(_label(m, dots))}</td><td class='loss'>{move_went(m)}</td>"
                    f"<td>{_e(SHORT.get(m['gap'], ''))}</td><td class='ex'>{_e(extra)}</td></tr>")
    more = f"<p class='more'>+{len(costly) - limit} more in the text report</p>" if len(costly) > limit else ""
    return f"<table class='costly'>{''.join(rows)}</table>{more}" if rows else "<p class='more'>No costly moves.</p>"


# ---------------------------------------------------------------- page


CSS = """
:root { color-scheme: light; --bg:#f4f3ef; --card:#fcfcfb; --fg:#0b0b0b; --fg2:#52514e; --muted:#8a8984; --line:#e4e3dd;
  --s1:#2a78d6; --s1-soft:#2a78d622; --tt:#8a898433; --bar-tt:#b4b3ad; --accent:#2a78d6; --good:#1e8e3e; --warn:#c26a00; --s2:#eb6834; --s1-light:#86b6ef; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { color-scheme: dark; --bg:#121211; --card:#1a1a19;
  --fg:#f0efec; --fg2:#c3c2b7; --muted:#8f8e86; --line:#2e2e2b; --s1:#3987e5; --s1-soft:#3987e533; --tt:#8f8e8622; --bar-tt:#5d5c57; --accent:#3987e5; --good:#3fa95c; --warn:#d98a1c; --s2:#d95926; --s1-light:#184f95; } }
:root[data-theme="dark"] { color-scheme: dark; --bg:#121211; --card:#1a1a19; --fg:#f0efec; --fg2:#c3c2b7; --muted:#8f8e86;
  --line:#2e2e2b; --s1:#3987e5; --s1-soft:#3987e533; --tt:#8f8e8622; --bar-tt:#5d5c57; --accent:#3987e5; --good:#3fa95c; --warn:#d98a1c; --s2:#d95926; --s1-light:#184f95; }
* { box-sizing:border-box; }
html, body { height:100%; margin:0; }
body { background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; overflow-x:hidden; overflow-y:auto; max-width:none; padding:0; }
html { scroll-behavior:smooth; }
.playstory { font-size:13px; font-weight:600; margin-left:10px; padding:4px 12px; border-radius:999px; background:var(--accent); color:#fff;
  text-decoration:none; vertical-align:middle; white-space:nowrap; } .playstory:hover { filter:brightness(1.1); } .below { padding:4px 16px 24px; } a.kpi.qs { color:inherit; text-decoration:none; cursor:pointer; } a.kpi.qs:hover { border-color:var(--fg2); }
.dash { height:100vh; display:grid; grid-template-rows:auto auto 1fr; gap:10px; padding:12px 16px; }
header { display:flex; align-items:center; gap:18px; }
header h1 { font-size:18px; margin:0; letter-spacing:-0.01em; } header .meta { color:var(--fg2); font-size:13px; }
.kpis { display:flex; gap:10px; margin-left:auto; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:6px 12px; min-width:118px; }
.kpi b { display:block; font-size:20px; font-variant-numeric:tabular-nums; letter-spacing:-0.02em; }
.kpi span { color:var(--fg2); font-size:12px; }
.verdict { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(0, 1fr); gap:10px; }
.verdict div { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px 14px 8px 12px;
  display:grid; grid-template-columns:auto 1fr; gap:0 10px; align-items:start; font-size:13.5px; }
.verdict i { font-style:normal; width:22px; height:22px; border-radius:50%; display:grid; place-items:center; font-weight:700;
  color:#fff; grid-row:span 2; margin-top:2px; }
.verdict .v-good i { background:var(--good); } .verdict .v-watch i { background:var(--warn); } .verdict .v-strike i { background:var(--fg2); color:var(--bg); } .gtype { display:inline-block; margin:8px 0 0; padding:3px 11px; border-radius:20px; font-size:13px; font-weight:700;
  background:rgba(127,127,127,.15); }
.gtype small { font-weight:400; opacity:.8; margin-left:8px; font-size:12.5px; }
.gtype.swindle, .gtype.stomp { background:color-mix(in srgb, #2f9e44 28%, transparent); }
.gtype.swindled, .gtype.stomped { background:color-mix(in srgb, #e03131 26%, transparent); }
.gtype.crazy { background:color-mix(in srgb, #f08c00 30%, transparent); }
.verdict .v-what { margin-top:6px; font-size:11.5px; color:var(--muted); }
.verdict .v-break { margin-top:6px; color:var(--muted); }
.strike line { stroke:var(--fg2); stroke-width:1.5; stroke-dasharray:3 3; } .strike text { fill:var(--fg2); font-size:10.5px; font-weight:600; }
.verdict b { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--fg2); }
.verdict p { margin:0; color:var(--fg); } .verdict [data-moment] { cursor:pointer; } .verdict [data-moment]:hover { border-color:var(--accent); }
.takeaways { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:10px;
  padding:8px 14px; display:grid; grid-template-columns:repeat(2, 1fr); gap:4px 24px; font-size:13px; }
.takeaways p { margin:0; color:var(--fg2); } .takeaways b { color:var(--fg); }
.main { display:grid; grid-template-columns:minmax(360px, 0.9fr) 1.1fr; gap:10px; min-height:0; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.card h3 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); font-weight:600; }
.left { display:flex; flex-direction:column; }
.tabs { display:flex; gap:6px; margin-bottom:6px; }
.tab { flex:1; background:transparent; color:var(--fg2); border:1px solid var(--line); border-radius:8px; padding:5px 8px; cursor:pointer; font:inherit; font-size:13px; text-align:left; }
.tab b { color:var(--fg); } .tab small { display:block; color:var(--muted); font-size:11px; }
.tab.on { border-color:var(--accent); background:var(--s1-soft); }
.moment { display:none; flex:1; min-height:0; flex-direction:column; } .moment.on { display:flex; }
.moment .verdict { margin:0 0 4px; font-size:13px; color:var(--fg2); }
.player { display:flex; flex-direction:column; flex:1; min-height:0; max-width:none; }
.stage { flex:1; min-height:0; display:flex; justify-content:center; }
.stage .f { height:100%; } .stage svg { height:100%; width:auto; max-width:100%; }
.cap { min-height:3.9em; font-size:13px; margin:6px 0 4px; }
.chips { margin-top:4px; } .chip { font-size:12px; padding:2px 8px; }
.right { display:grid; grid-template-rows:1.6fr auto; gap:10px; min-height:0; }
.right.with-thoughts { grid-template-rows:1.6fr auto auto; }
.with-thoughts .bar-row { margin-bottom:3px; font-size:12px; gap:1px 8px; } .with-thoughts .bar-row:last-child { margin-bottom:0; } .with-thoughts .bar-track, .with-thoughts .bar { height:7px; }
.with-thoughts .costly td { padding:1px 4px; }
.thoughts .ins { display:grid; grid-template-columns:1fr 1fr; gap:6px 18px; }
.thoughts p { margin:0; font-size:12.5px; line-height:1.35; color:var(--fg2); border-left:3px solid var(--s1-soft); padding-left:8px; }
.thoughts p b { color:var(--fg); display:block; font-size:12px; }
.thoughts p[data-moment] { cursor:pointer; } .thoughts p[data-moment]:hover { border-left-color:var(--accent); }
.timeline .crit { fill:var(--s2); }
.legend-row .tri { display:inline-block; width:0; height:0; background:none; border-left:5px solid transparent;
  border-right:5px solid transparent; border-bottom:7px solid var(--s2); border-radius:0; }
.timecard { margin:22px 0 6px; } .timecard h3 { margin-top:0; }
.tc-lead { margin:0 0 10px; color:var(--fg); font-size:14px; }
.tc-bars { display:grid; gap:6px; max-width:620px; }
.tc-row { display:grid; grid-template-columns:minmax(0, 210px) 1fr 52px; gap:10px; align-items:center; font-size:13px; color:var(--fg2); }
.tc-row b { text-align:right; color:var(--fg); font-variant-numeric:tabular-nums; }
.tc-track { height:10px; border-radius:4px; background:var(--s1-soft); overflow:hidden; display:flex; }
.tc-bar { background:var(--s1); border-radius:0 4px 4px 0; } .tc-bar.crit { background:var(--s2); }
.tc-list { margin:10px 0 0; padding-left:18px; font-size:13.5px; color:var(--fg2); } .tc-list li { margin:4px 0; }
.tc-list li b { color:var(--fg); } .tc-list li.rushed::marker { color:var(--s2); }
.bottom { display:grid; grid-template-columns:1fr; gap:10px; min-height:0; }
.gapbars { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:0 18px; align-items:end; } .gapbars .bar-label { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; } .gapbars .more { margin:0; align-self:center; }
.timeline { width:100%; flex:1; min-height:0; }
.timeline .grid { stroke:var(--line); stroke-width:1; } .timeline .mid { stroke:var(--muted); stroke-dasharray:3 3; stroke-width:1; }
.timeline .ax { fill:var(--muted); font-size:10px; } .timeline .tt { fill:var(--tt); } .timeline .ttl { fill:var(--muted); font-size:10px; }
.timeline .wp { fill:none; stroke:var(--s1); stroke-width:2; stroke-linejoin:round; }
.timeline .drop { stroke:var(--fg2); stroke-width:1.5; stroke-dasharray:2 2; }
.timeline .dot { fill:var(--s1); stroke:var(--card); stroke-width:2; } .timeline .tt-dot { fill:var(--bar-tt); }
.timeline .hit { fill:transparent; cursor:pointer; } .timeline .num { fill:var(--fg); font-size:11px; font-weight:700; }
.timeline .pt:hover .dot { stroke:var(--fg); }
.timeline .base { stroke:var(--line); stroke-width:1; } .timeline .plab { fill:var(--fg2); font-size:10.5px; font-weight:600; }
.timeline .clk { fill:none; stroke-width:2; stroke-linejoin:round; } .timeline .clk.you { stroke:var(--s1); } .timeline .clk.opp { stroke:var(--s2); }
.timeline .endl { fill:var(--fg2); font-size:10px; }
.timeline .bar-t { fill:var(--s1-light); } .timeline .bar-t.costly { fill:var(--s1); }
.timeline .xh { stroke:var(--fg2); stroke-width:1; pointer-events:none; }
.legend-row .k { display:inline-block; width:14px; height:0; border-top:2px solid; margin-right:4px; vertical-align:3px; }
.legend-row .sq { display:inline-block; width:8px; height:10px; border-radius:1.5px; margin-right:4px; vertical-align:-1px; }
.legend-row { flex-wrap:wrap; row-gap:2px; }
#tip .row { display:flex; gap:6px; align-items:baseline; } #tip .row b { font-variant-numeric:tabular-nums; }
#tip .row i { display:inline-block; width:10px; height:0; border-top:2px solid; vertical-align:3px; } #tip .row span { opacity:.75; }
#tip .head { font-weight:700; margin-bottom:2px; }
.meter { display:flex; height:6px; gap:2px; margin:4px 0 2px; } .meter span { display:block; height:6px; }
.meter span:first-child { border-radius:3px 0 0 3px; } .meter span:last-child { border-radius:0 3px 3px 0; }
.meter span:only-child { border-radius:3px; }
.kpi small { display:block; color:var(--muted); font-size:11px; }
.kpi.qs { min-width:250px; } .kpi.qs > span { display:block; margin-bottom:2px; }
.kpi .q { display:grid; grid-template-columns:72px 1fr 34px; align-items:center; gap:6px; font-size:11.5px; line-height:1.3; }
.kpi .q .qn { color:var(--fg2); } .kpi .q b { font-size:12px; text-align:right; letter-spacing:0; display:inline; }
.kpi .q .meter { margin:0; }
.legend-row { display:flex; gap:14px; font-size:12px; color:var(--fg2); margin-top:4px; }
.legend-row i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:-1px; }
.bar-row { display:grid; grid-template-columns:1fr auto; gap:2px 8px; margin:0 0 7px; font-size:13px; }
.bar-row .bar-track { grid-column:1 / -1; }
.bar-label { color:var(--fg2); } .bar-track { display:block; height:10px; background:var(--s1-soft); border-radius:0 4px 4px 0; }
.bar { display:block; height:10px; background:var(--s1); border-radius:0 4px 4px 0; min-width:2px; }
.bar-row.muted .bar { background:var(--bar-tt); }
.bar-val { font-variant-numeric:tabular-nums; min-width:4.2em; text-align:right; } .bar-val small { color:var(--muted); }
table.costly { width:100%; border-collapse:collapse; font-size:12.5px; }
.costly td { padding:2px 4px; border-bottom:1px solid var(--line); white-space:nowrap; }
.costly td.mv { font-weight:600; } .costly td.loss { font-variant-numeric:tabular-nums; } .costly td.ex { color:var(--fg2); overflow:hidden; text-overflow:ellipsis; max-width:9em; }
.costly tr.muted td { color:var(--muted); } .costly tr[data-moment] { cursor:pointer; } .costly tr[data-moment]:hover td { background:var(--s1-soft); }
.badge { display:inline-block; width:16px; height:16px; line-height:16px; border-radius:50%; background:var(--accent); color:#fff; font-size:11px; text-align:center; margin-right:5px; }
.more { color:var(--muted); font-size:12px; margin:4px 0 0; }
@media (max-width: 1150px), (max-height: 640px) {
  body { overflow:auto; } .dash { height:auto; } header { flex-wrap:wrap; } .kpis { margin-left:0; flex-wrap:wrap; }
  .takeaways, .verdict { grid-template-columns:1fr; grid-auto-flow:row; } .main { grid-template-columns:1fr; } .right, .right.with-thoughts { grid-template-rows:auto auto auto; } .with-thoughts .bar-row { margin-bottom:3px; font-size:12px; gap:1px 8px; } .with-thoughts .bar-row:last-child { margin-bottom:0; } .with-thoughts .bar-track, .with-thoughts .bar { height:7px; }
.with-thoughts .costly td { padding:1px 4px; }
.thoughts .ins { grid-template-columns:1fr; }
  .moment { min-height:70vh; } .bottom { grid-template-columns:1fr; } }
#tip { position:fixed; pointer-events:none; background:var(--fg); color:var(--bg); font-size:12px; padding:4px 8px; border-radius:6px; display:none; z-index:9; max-width:320px; }
.moment details { display:none; }
"""

JS = """
const players = [];
for (const p of document.querySelectorAll('.player')) {
  const meta = JSON.parse(p.dataset.frames), fs = p.querySelectorAll('.f'), cap = p.querySelector('.cap');
  const chips = [...p.querySelectorAll('.chip')], count = p.querySelector('.count'), play = p.querySelector('.play');
  const st = { i: 0, timer: null, playing: false };
  const show = (n) => {
    st.i = (n + fs.length) % fs.length;
    fs.forEach((f, j) => f.classList.toggle('on', j === st.i));
    fs[st.i].querySelector('svg').style.setProperty('--k', meta[st.i].k || 'transparent');
    cap.innerHTML = meta[st.i].c; count.textContent = (st.i + 1) + ' / ' + fs.length;
    chips.forEach((c, j) => c.classList.toggle('on', +c.dataset.at <= st.i && (j + 1 === chips.length || +chips[j + 1].dataset.at > st.i)));
    clearTimeout(st.timer); if (st.playing) st.timer = setTimeout(() => show(st.i + 1), meta[st.i].d);
  };
  const setPlaying = (v) => { st.playing = v; play.innerHTML = v ? '&#10073;&#10073;' : '&#9654;&#9654;'; show(st.i); };
  play.onclick = () => setPlaying(!st.playing);
  p.querySelector('.prev').onclick = () => { setPlaying(false); show(st.i - 1); };
  p.querySelector('.next').onclick = () => { setPlaying(false); show(st.i + 1); };
  chips.forEach(c => c.onclick = () => { setPlaying(false); show(+c.dataset.at); });
  show(0); players.push({ setPlaying });
}
const tabs = [...document.querySelectorAll('.tab')], moments = [...document.querySelectorAll('.moment')];
const open = (k) => {
  tabs.forEach((t, j) => t.classList.toggle('on', j === k));
  moments.forEach((m, j) => m.classList.toggle('on', j === k));
  players.forEach((p, j) => p.setPlaying(j === k));
};
tabs.forEach((t, k) => t.onclick = () => open(k));
document.querySelectorAll('[data-moment]').forEach(el => el.addEventListener('click', () => open(+el.dataset.moment)));
if (tabs.length) open(0);
const tip = document.getElementById('tip');
for (const svg of document.querySelectorAll('svg.timeline')) {
  const pts = JSON.parse(svg.nextElementSibling.textContent), xh = svg.querySelector('.xh');
  const hide = () => { xh.setAttribute('visibility', 'hidden'); tip.style.display = 'none'; };
  svg.addEventListener('pointermove', (e) => {
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    let best = pts[0];
    for (const q of pts) if (Math.abs(q.x - p.x) < Math.abs(best.x - p.x)) best = q;
    xh.setAttribute('x1', best.x); xh.setAttribute('x2', best.x); xh.setAttribute('visibility', 'visible');
    tip.textContent = '';
    const head = document.createElement('div'); head.className = 'head'; head.textContent = best.label; tip.append(head);
    for (const [value, label, key] of best.rows) {
      const row = document.createElement('div'); row.className = 'row';
      const k = document.createElement('i');
      k.style.borderColor = key === 'opp' ? 'var(--s2)' : key ? 'var(--s1)' : 'transparent';
      const b = document.createElement('b'); b.textContent = value;
      const span = document.createElement('span'); span.textContent = label;
      row.append(k, b, span); tip.append(row);
    }
    tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 200) + 'px';
    tip.style.top = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8) + 'px';
  });
  svg.addEventListener('pointerleave', hide);
}
document.querySelectorAll('[data-tip]').forEach(el => {
  el.addEventListener('mousemove', e => { tip.textContent = el.dataset.tip; tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 12, innerWidth - 330) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; });
  el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
});
"""


def render(report: dict, analysis: dict, thinking: dict | None, n: int = 3, line_for=None) -> str:
    g, s = report["game"], report["summary"]
    me = chess.WHITE if g["my_color"] == "white" else chess.BLACK
    dots = "." if me == chess.WHITE else "..."
    windows = {w["ply"]: w for w in (thinking or {}).get("windows", [])}
    chosen = pick(report, analysis, n)
    plies = [c["move"]["ply"] for c in chosen]

    tabs, panes = [], []
    for k, c in enumerate(chosen):
        m, rec = c["move"], c["record"]
        sub = (f"&minus;{pct(m['wp_loss'])} &middot; {_e(SHORT.get(m['gap'], ''))}" if c["why"] == "costly"
               else f"only move (+{pct(sharpness(rec))})")
        tabs.append(f"<button class='tab'><b><span class='badge'>{k + 1}</span>{_e(_label(m, dots))}</b><small>{sub}</small></button>")
        r = reel(m, rec, windows.get(m["ply"]), me, _last_move(windows, m["ply"], rec), line_for)
        panes.append(f"<div class='moment'>{player_html(r)}</div>")
    left = ("<div class='tabs'>" + "".join(tabs) + "</div>" + "".join(panes)) if chosen else \
        "<p class='more'>No critical moments: no costly moves and no sharp positions.</p>"

    tt_lost = s["by_gap"].get("time_trouble", {}).get("wp_lost", 0)
    thinking_lost = sum(b["wp_lost"] for k, b in s["by_gap"].items() if k != "time_trouble")
    real = s["q3_real_threats"]
    thinking_n = sum(b["count"] for k, b in s["by_gap"].items() if k != "time_trouble")
    tt_n = s["by_gap"].get("time_trouble", {}).get("count", 0)
    kpis = [
        (f"{thinking_n}", "costly moves with time to think"),
    ] + ([(f"{tt_n}", f"costly in time trouble (under {report['thresholds'].get('time_trouble', 30):.0f}s)")] if tt_n else []) + [
        (f"{s['q3_spotted'] + s.get('q3_handled', 0)}/{real}" if real else "–", "real threats seen or stopped"),
        (f"{s['q4_best_considered']}/{s['q4_positions']}" if s["q4_positions"] else "–", "best move among yours"),
    ]
    kpi_html = "".join(f"<div class='kpi'><b>{v}</b><span>{_e(t)}</span></div>" for v, t in kpis[:-2])
    kpi_html += questions_tile(s) + best_meter(s)
    thoughts = thoughts_card(report, plies)
    aagaard = questions.render(questions.compute([{"id": g["id"], "report": report}], examples=6),
                               title="Aagaard's questions, this game", link="lichess",
                               sub="Every position where the engine found something to see, and whether your arrows "
                                   "and words saw it. Click a board to open that position on lichess.")
    t = classify(analysis, g)
    gtype = (f"<p class='gtype {t['kind']}'>{_e(t['label'])}<small>{_e(t['why'])}</small></p>" if t else "")
    verdict = verdict_html(report, plies)  # when there is a summary it replaces the takeaways strip
    take = "".join(f"<p>{t}</p>" for t in takeaways(report, dots)) or "<p>No costly moves this game.</p>"

    return f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thought Coach {_e(g['id'])}</title>{THEME_HEAD}<style>{REEL_CSS}{CSS}{questions.CSS}</style></head><body>{THEME_BUTTON}
<div class="dash">
  <header>
    <div><h1>Thought Coach &middot; {_e(g['white'])} vs {_e(g['black'])} <a class="playstory" href="{_e(game_url(g['id'], story=True))}">&#9654; Watch your game story</a></h1>
    <div class="meta">{_e(g['result'])} &middot; {_e(g.get('time_control') or '?')} &middot; you played {_e(g['my_color'])}
      &middot; {s['moves_reviewed']} moves reviewed, {s['moves_with_thoughts']} with thoughts</div>{gtype}</div>
    <div class="kpis">{kpi_html}</div>
  </header>
  {verdict if verdict else f'<section class="takeaways">{take}</section>'}
  <div class="main">
    <div class="card left"><h3>Critical moments</h3>{left}</div>
    <div class="right{' with-thoughts' if thoughts else ''}">
      <div class="card"><h3>The game, move by move</h3>{game_chart(report, analysis, plies, dots)}
        <div class="legend-row"><span><i class="k" style="border-color:var(--s1)"></i>you</span>
        <span><i class="k" style="border-color:var(--s2)"></i>them (clock)</span>
        <span><i style="background:var(--s1)"></i>costly move (number = moment)</span>
        <span><i class="sq" style="background:var(--s1-light)"></i><i class="sq" style="background:var(--s1)"></i>think time, dark = costly</span>
        <span><i class="sq" style="background:var(--tt);width:14px"></i>time trouble</span>
        <span><i class="tri"></i>only one move held</span></div></div>
      {thoughts}
      <div class="bottom">
        <div class="card"><h3>What went wrong, by cause</h3><div class="gapbars">{gap_bars(report)}</div></div>
      </div>
    </div>
  </div>
</div>
<div class="below">{time_card(report, analysis)}{aagaard}</div>
<div id="tip"></div>
<script>{JS}</script>
</body></html>"""
