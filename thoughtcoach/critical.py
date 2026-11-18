"""Critical moments: the few positions that decided the game, played back one idea at a time.

Each moment is a short looping animation (like a GIF, with pause and step controls). It runs:
  1. the position, with the opponent's last move
  2. what you thought: opponent ideas you drew or said, squares you marked, then each move you
     considered, one at a time, with its engine line played out and its win probability
  3. the move you played, with its line
  4. the engine: the best move, good moves you missed, and the opponent's real threat
One arrow per frame, coloured by what it is, with a caption saying what it means.
Below it (folded away): a table of the same moves with WP and lines, and what you said.

Moments are the costliest moves you had time to think about (WP loss >= the costly-move
threshold, not in time trouble). If there are fewer than `n`, the rest are the sharpest positions
(where the best move was much better than the next one), then costly moves made in time trouble.
"""

from __future__ import annotations

import html
import json
import re

import chess
import chess.svg

from .gaps import GAP_LABEL
from .fmt import pct
from .fmt import THEME_BUTTON, THEME_HEAD

GREEN, ORANGE, RED = "#15781bdd", "#e68a00dd", "#c0392bcc"
BLUE, LIGHT_BLUE, PURPLE, YELLOW = "#1f4fa8ee", "#3c78d8cc", "#7b2cbfcc", "#f4d03f99"
PICKUP = "#17a2b899"  # teal: a piece you picked up
WEAK = "#a0522daa"  # brown: weaknesses the engine goes for (Aagaard Q1)
WORST = "#e83e8caa"  # pink: your worst piece (Aagaard Q2)
LINE_PLIES = 4  # how far each idea's line is played out
SIZE = 400


def sharpness(record: dict) -> float:
    """How much better the best move was than the second best (0 if there is only one line)."""
    wps = sorted((c["wp"] for c in record.get("candidates") or []), reverse=True)
    return round(wps[0] - wps[1], 3) if len(wps) > 1 else 0.0


DECISIVE = 0.20  # a time-trouble move losing this much still gets a board


def pick(report: dict, analysis: dict, n: int = 3) -> list[dict]:
    """[{move: gap-report move, record: engine record, why: 'costly'|'sharp'}], in game order."""
    records = {r["ply"]: r for r in analysis["moves"]}
    by_loss = sorted((m for m in report["moves"] if m["mistake"]), key=lambda m: -m["wp_loss"])
    costly = [m for m in by_loss if m["gap"] != "time_trouble"][:n]
    chosen = [{"move": m, "why": "costly"} for m in costly]
    if len(chosen) < n:
        taken = {m["ply"] for m in costly}
        rest = [m for m in report["moves"] if m["ply"] not in taken and not m["mistake"]
                and sharpness(records[m["ply"]]) >= 0.05]
        rest.sort(key=lambda m: -sharpness(records[m["ply"]]))
        chosen += [{"move": m, "why": "sharp"} for m in rest[: n - len(chosen)]]
    in_tt = [m for m in by_loss if m["gap"] == "time_trouble"]
    if len(chosen) < n:
        chosen += [{"move": m, "why": "costly"} for m in in_tt][: n - len(chosen)]
    elif in_tt and in_tt[0]["wp_loss"] >= DECISIVE and all(in_tt[0]["wp_loss"] > c["move"]["wp_loss"] for c in chosen):
        # A move that decided the game is worth a board even when it was played in time trouble.
        chosen[-1] = {"move": in_tt[0], "why": "costly"}
    for c in chosen:
        c["record"] = records[c["move"]["ply"]]
    return sorted(chosen, key=lambda c: c["move"]["ply"])


def _move(board: chess.Board, uci: str) -> chess.Move:
    """A move from a drawn arrow; an arrow onto the last rank means promoting to a queen."""
    m = chess.Move.from_uci(uci)
    if m not in board.legal_moves:
        queen = chess.Move(m.from_square, m.to_square, promotion=chess.QUEEN)
        if queen in board.legal_moves:
            return queen
    return m


def _sq(name: str | None):
    return chess.parse_square(name) if name else None


def _last_move(window_by_ply: dict, ply: int, record: dict) -> chess.Move | None:
    """The opponent's move just before this position, for highlighting."""
    prev = window_by_ply.get(ply - 2)
    if not prev or not record.get("opponent_move_san"):
        return None
    try:
        board = chess.Board(prev["fen"])
        board.push_uci(prev["move_uci"])
        return board.parse_san(record["opponent_move_san"])
    except ValueError:
        return None


def line_moves(board: chess.Board, line_san: str | None, first: chess.Move | None = None) -> list[chess.Move]:
    """The first LINE_PLIES moves of a SAN line like '16. f4 Nf6 17. Bf2', checked on `board`."""
    moves, b = [], board.copy()
    for tok in re.sub(r"\d+\.(\.\.)?", " ", line_san or "").split():
        try:
            m = b.parse_san(tok)
        except ValueError:
            break
        moves.append(m)
        b.push(m)
        if len(moves) >= LINE_PLIES:
            break
    if first is not None and (not moves or moves[0] != first):
        moves = [first]
    return moves


class Reel:
    """Frames of one moment: board svg, caption, colour of the idea, how long to show it (ms)."""

    def __init__(self, me: chess.Color):
        self.me, self.frames, self.chapters = me, [], []

    def add(self, board, caption, color="", arrows=(), lastmove=None, fill=None, ms=1800):
        svg = chess.svg.board(board, orientation=self.me, arrows=list(arrows), lastmove=lastmove,
                              fill=fill or {}, size=SIZE)
        self.frames.append({"svg": svg, "c": caption, "k": color[:7], "d": ms})

    def chapter(self, label, color):
        self.chapters.append({"at": len(self.frames), "label": label, "k": color[:7]})

    def idea(self, board, move, color, label, title, verdict, line_san=None, lastmove=None):
        """One idea: its arrow alone, then its line move by move, ending on the verdict."""
        self.chapter(label, color)
        self.add(board, title, color, [chess.svg.Arrow(move.from_square, move.to_square, color=color)],
                 lastmove=lastmove, ms=2200)
        moves = line_moves(board, line_san, move)
        b = board.copy()
        for i, m in enumerate(moves):
            b.push(m)
            last = i == len(moves) - 1
            text = html.escape(board.variation_san(moves[: i + 1]))
            caption = f"{title}<br><span class='ln'>{text}</span>" + (f"<br><b>{verdict}</b>" if last and verdict else "")
            self.add(b, caption, color, lastmove=m, ms=3000 if last else 1100)


def _wp_text(wp, best_wp) -> str:
    if wp is None:
        return ""
    loss = best_wp - wp
    tail = "the best move" if loss <= 0.005 else ("also good" if loss <= 0.05 else f"gives up {pct(loss)}")
    return f"WP {pct(wp)} &mdash; {tail}"


def reel(move: dict, record: dict, window: dict | None, me: chess.Color, lastmove, line_for=None) -> Reel:
    board = chess.Board(record["fen"])
    shapes = (window or {}).get("shapes", [])
    played = chess.Move.from_uci(record["move_uci"])
    best_wp = record["wp_before"]
    r = Reel(me)

    # 1. The position
    r.chapter("position", "")
    opp = f" They just played {html.escape(record['opponent_move_san'])}." if record.get("opponent_move_san") else ""
    side = "White" if me == chess.WHITE else "Black"
    clock = move.get("clock_before")
    if move.get("gap") == "time_trouble" and clock is not None:
        opp += f" You had {clock:.0f}s on your clock."
    r.add(board, f"{side} to move, WP {pct(best_wp)}.{opp}", "", lastmove=lastmove, ms=2500)

    said = {}
    for s in shapes:
        if s.get("utterance") and s["from"] and s["to"]:
            said.setdefault((s["from"], s["to"]), s["utterance"])

    def quote(m: chess.Move) -> str:
        u = said.get((chess.square_name(m.from_square), chess.square_name(m.to_square)))
        return f"<br><i>&ldquo;{html.escape(u)}&rdquo;</i>" if u else ""

    # 2. What you thought
    seen = set()
    for s in shapes:
        if s["kind"] == "opponent_idea" and s["from"] and s["to"] and (s["from"], s["to"]) not in seen:
            seen.add((s["from"], s["to"]))
            name = s["move_san"] or f"{s['from']}-{s['to']}"
            u = f"<br><i>&ldquo;{html.escape(s['utterance'])}&rdquo;</i>" if s.get("utterance") else ""
            r.chapter(f"their {name}", PURPLE)
            r.add(board, f"You saw their idea: <b>{html.escape(name)}</b>{u}", PURPLE,
                  [chess.svg.Arrow(_sq(s["from"]), _sq(s["to"]), color=PURPLE)], lastmove=lastmove, ms=2500)
    marked = list(dict.fromkeys(s["from"] for s in shapes if s["kind"] in ("key_square", "worst_piece") and s["from"]))
    if marked:
        r.chapter("squares", YELLOW)
        r.add(board, f"You marked: {', '.join(marked)}", YELLOW, lastmove=lastmove,
              fill={_sq(q): YELLOW for q in marked}, ms=2200)

    names = {"P": "pawn", "N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king"}
    for pu in move.get("picked_up", []):
        if pu["moved"]:
            continue
        name = f"{names.get(pu['piece'], 'piece')} on {pu['square']}"
        why = f" &mdash; the engine's best move, {html.escape(record['best_move_san'])}, was with this piece"             if pu["best_piece"] else ""
        r.chapter(f"picked up {pu['piece']}{pu['square']}", PICKUP)
        r.add(board, f"You picked up the <b>{name}</b> but didn't move it{why}", PICKUP, lastmove=lastmove,
              fill={_sq(pu["square"]): PICKUP}, ms=2600)

    by_uci = {c["move_uci"]: c for c in record["candidates"]}
    for d in move["drawn_candidates"]:
        if not d.get("move_uci") or d["played"]:
            continue
        m = _move(board, d["move_uci"])
        line = by_uci.get(m.uci(), {}).get("line_san")
        if line is None and line_for is not None:
            line = line_for(record["fen"], m.uci())
        r.idea(board, m, LIGHT_BLUE, f"you had {d['move_san']}",
               f"You considered <b>{html.escape(d['move_san'])}</b>{quote(m)}", _wp_text(d["wp"], best_wp), line, lastmove)

    # 3. What you played
    r.idea(board, played, BLUE, f"played {move['move_san']}",
           f"You played <b>{html.escape(move['move_san'])}</b>{quote(played)}",
           f"WP {pct(record['wp_after'])} (&minus;{pct(record['wp_loss'])})", record.get("played_line_san"), lastmove)

    # 4. The engine
    found = set(move["q4"]["found"])
    best = next((c for c in record["candidates"] if c["move_san"] == record["best_move_san"]), None)
    if best and best["move_uci"] != record["move_uci"] and best["move_san"] in found:
        m = chess.Move.from_uci(best["move_uci"])  # its line was already shown as one of yours
        r.chapter(f"best {best['move_san']}", GREEN)
        r.add(board, f"Engine's best: <b>{html.escape(best['move_san'])}</b> &mdash; you had it, then played "
                     f"{html.escape(move['move_san'])}<br><b>WP {pct(best['wp'])} vs {pct(record['wp_after'])}</b>",
              GREEN, [chess.svg.Arrow(m.from_square, m.to_square, color=GREEN)], lastmove=lastmove, ms=3500)
    elif best and best["move_uci"] != record["move_uci"]:
        had = "not considered"
        r.idea(board, chess.Move.from_uci(best["move_uci"]), GREEN, f"best {best['move_san']}",
               f"Engine's best: <b>{html.escape(best['move_san'])}</b> &mdash; {had}",
               f"WP {pct(best['wp'])}", best.get("line_san"), lastmove)
    missed = [c for c in record["candidates"] if c["move_san"] in move["q4"]["missed"] and c is not best][:2]
    for c in missed:
        r.idea(board, chess.Move.from_uci(c["move_uci"]), ORANGE, f"missed {c['move_san']}",
               f"Also good, not considered: <b>{html.escape(c['move_san'])}</b>",
               f"WP {pct(c['wp'])}", c.get("line_san"), lastmove)
    q3, threat = move["q3"], record.get("threat")
    if threat and q3["status"] in ("spotted", "handled", "not_drawn"):
        state = {"spotted": "you saw it", "handled": "your move dealt with it", "not_drawn": "not spotted, and your move didn't stop it"}
        passed = board.copy()
        passed.push(chess.Move.null())  # their threat: what they would play if it were their move
        r.idea(passed, chess.Move.from_uci(threat["move_uci"]), RED, f"threat {threat['move_san']}",
               f"Their threat if you did nothing: <b>{html.escape(threat['move_san'])}</b> &mdash; {state[q3['status']]}",
               f"would cost you {pct(threat['swing'])}", threat.get("line_san"))

    # 5. Aagaard's other two questions
    weak = (move.get("q1") or {}).get("weaknesses") or []
    if weak:
        r.chapter("weaknesses", WEAK)
        parts = [f"<b>{w['square']}</b> ({'your' if w['side'] == 'yours' else 'their'} {html.escape(w['kind'])}"
                 f"{', you saw it' if w.get('you') else ''})" for w in weak]
        r.add(board, "Weaknesses the engine goes for: " + ", ".join(parts), WEAK, lastmove=lastmove,
              fill={_sq(w["square"]): WEAK for w in weak}, ms=3200)
    worst = (move.get("q2") or {}).get("worst")
    if worst:
        r.chapter(f"worst {worst['piece']}", WORST)
        plan = (f"The engine's plan improves it with <b>{html.escape(worst['engine_move'])}</b>."
                if worst.get("engine_move") else "The engine has no quick way to improve it.")
        seen = {"named": " You named it.", "moved it in a candidate": " You considered a move with it."}.get(worst.get("you"), "")
        r.add(board, f"Your worst piece: the <b>{worst['piece']} on {worst['square']}</b>, reaching "
                     f"{pct(worst['activity'])} of its squares. {plan}{seen}", WORST, lastmove=lastmove,
              fill={_sq(worst["square"]): WORST}, ms=3200)
    return r


def _table(move: dict, record: dict) -> str:
    """Engine's good moves, then your other moves, with WP and lines."""
    rows = []
    found = set(move["q4"]["found"])
    drawn = {d["move_san"]: d for d in move["drawn_candidates"] if d["legal"]}
    by_san = {c["move_san"]: c for c in record["candidates"]}
    for san in move["q4"]["engine"]:
        c = by_san.get(san, {})
        tags = ["best"] if san == record["best_move_san"] else []
        tags.append("you had it" if san in found else "missed")
        if san == move["move_san"]:
            tags.append("played")
        rows.append((san, c.get("wp"), ", ".join(tags), c.get("line_san", ""), "good" if san in found else "missed"))
    for san, d in drawn.items():
        if san in move["q4"]["engine"]:
            continue
        tags = ["played"] if d["played"] else []
        tags.append(f"you had it ({d['how']})")
        if d.get("extra_good"):
            tags.append("also good")
        line = record.get("played_line_san", "") if d["played"] else ""
        rows.append((san, d["wp"], ", ".join(tags), line, "good" if d.get("extra_good") else "bad"))
    if move["move_san"] not in move["q4"]["engine"] and move["move_san"] not in drawn:
        rows.append((move["move_san"], record["wp_after"], "played (not considered)", record.get("played_line_san", ""), "bad"))
    body = "".join(
        f"<tr class='{cls}'><td class='san'>{html.escape(san)}</td>"
        f"<td>{'' if wp is None else pct(wp)}</td><td>{html.escape(tags)}</td>"
        f"<td class='line'>{html.escape(line or '')}</td></tr>"
        for san, wp, tags, line, cls in rows
    )
    return f"<table><tr><th>Move</th><th>WP</th><th></th><th>Line</th></tr>{body}</table>"


def _said(window: dict | None) -> list[str]:
    out = []
    for s in (window or {}).get("shapes", []):
        u = s.get("utterance")
        if u and u not in out:
            out.append(u)
    return out


def player_html(r: Reel) -> str:
    frames = "".join(f"<div class='f'>{f['svg']}</div>" for f in r.frames)
    meta = json.dumps([{"c": f["c"], "k": f["k"], "d": f["d"]} for f in r.frames])
    chips = "".join(
        f"<button class='chip' data-at='{c['at']}' style='--k:{c['k'] or '#888888'}'>{html.escape(c['label'])}</button>"
        for c in r.chapters
    )
    return (
        f"<div class='player' data-frames='{html.escape(meta, quote=True)}'>"
        f"<div class='stage'>{frames}</div><p class='cap'></p>"
        "<div class='controls'><button class='prev' title='back'>&#9664;</button>"
        "<button class='play' title='pause / play'>&#10073;&#10073;</button>"
        "<button class='next' title='next'>&#9654;</button><span class='count'></span></div>"
        f"<div class='chips'>{chips}</div></div>"
    )


def moment_html(i: int, c: dict, window: dict | None, me: chess.Color, lastmove, dots: str, line_for=None) -> str:
    move, record = c["move"], c["record"]
    if c["why"] == "costly":
        head = f"lost {pct(move['wp_loss'])} ({pct(move['wp_before'])} &rarr; {pct(move['wp_after'])})"
        verdict = GAP_LABEL.get(move["gap"], "") if move["gap"] else ""
    else:
        head = f"only one good move: best was {pct(sharpness(record))} ahead of the next"
        verdict = "found it" if move["move_san"] == record["best_move_san"] else ""
    said = "".join(f"<li>&ldquo;{html.escape(u)}&rdquo;</li>" for u in _said(window))
    time = move.get("time_spent_pgn")
    return f"""
<section>
  <h2>{i}. {move['move_number']}{dots} {html.escape(move['move_san'])} <small>{head}{f" &middot; {time:.0f}s" if time is not None else ""}</small></h2>
  {f"<p class='verdict'>{html.escape(verdict)}</p>" if verdict else ""}
  {player_html(reel(move, record, window, me, lastmove, line_for))}
  <details><summary>All moves, lines and what you said</summary>
  {_table(move, record)}
  {f"<ul class='said'>{said}</ul>" if said else ""}
  </details>
</section>"""


LEGEND = (
    "<p class='legend'><i style='background:#3c78d8'></i>you considered <i style='background:#1f4fa8'></i>you played "
    "<i style='background:#17a2b8'></i>picked up, not moved <i style='background:#7b2cbf'></i>their idea you saw <i style='background:#15781b'></i>engine's best "
    "<i style='background:#e68a00'></i>good, missed <i style='background:#c0392b'></i>their threat</p>"
)

CSS = """
:root { --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e2dc; --good:#15781b; --bad:#b03a2e; --miss:#b86e00; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1b1b1a; --fg:#ecebe6; --muted:#a3a29b; --card:#252524; --line:#3a3a37; --good:#6cc070; --bad:#e57364; --miss:#f0a640; } }
:root[data-theme="dark"] { --bg:#1b1b1a; --fg:#ecebe6; --muted:#a3a29b; --card:#252524; --line:#3a3a37; --good:#6cc070; --bad:#e57364; --miss:#f0a640; }
body { background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:0 auto; max-width:560px; padding:16px; }
h1 { font-size:22px; margin:8px 0 2px; } h2 { font-size:18px; margin:0 0 4px; } h2 small { color:var(--muted); font-weight:normal; font-size:14px; }
.sub { color:var(--muted); margin:0 0 12px; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:16px 0; }
.verdict { color:var(--bad); font-weight:600; margin:0 0 8px; }
.stage .f { display:none; } .stage .f.on { display:block; }
.stage svg { width:100%; height:auto; display:block; box-sizing:border-box; border-left:8px solid var(--k, transparent); }
.cap { min-height:5.2em; margin:8px 0 4px; } .cap .ln { color:var(--muted); font-family:ui-monospace, monospace; font-size:14px; }
.controls { display:flex; gap:6px; align-items:center; }
.controls button, .chip { background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:4px 10px; cursor:pointer; font:inherit; font-size:13px; }
.count { color:var(--muted); font-size:13px; margin-left:6px; } .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.chip { border-left:5px solid var(--k); } .chip.on { background:var(--line); }
details { margin-top:12px; } summary { cursor:pointer; color:var(--muted); }
table { border-collapse:collapse; width:100%; margin-top:10px; font-size:14px; } th, td { text-align:left; padding:4px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
td.san { font-weight:600; white-space:nowrap; } .line { color:var(--muted); font-size:13px; }
tr.good td.san { color:var(--good); } tr.missed td.san { color:var(--miss); } tr.bad td.san { color:var(--bad); }
.said { margin:8px 0 0; padding-left:20px; color:var(--muted); }
.legend { font-size:13px; color:var(--muted); } .legend i { display:inline-block; width:12px; height:12px; border-radius:2px; margin:0 3px 0 8px; vertical-align:-1px; }
"""

JS = """
for (const p of document.querySelectorAll('.player')) {
  const meta = JSON.parse(p.dataset.frames), fs = p.querySelectorAll('.f'), cap = p.querySelector('.cap');
  const chips = [...p.querySelectorAll('.chip')], count = p.querySelector('.count'), play = p.querySelector('.play');
  let i = 0, timer = null, playing = true;
  const show = (n) => {
    i = (n + fs.length) % fs.length;
    fs.forEach((f, j) => f.classList.toggle('on', j === i));
    fs[i].querySelector('svg').style.setProperty('--k', meta[i].k || 'transparent');
    cap.innerHTML = meta[i].c;
    count.textContent = (i + 1) + ' / ' + fs.length;
    chips.forEach((c, j) => c.classList.toggle('on', +c.dataset.at <= i && (j + 1 === chips.length || +chips[j + 1].dataset.at > i)));
    clearTimeout(timer);
    if (playing) timer = setTimeout(() => show(i + 1), meta[i].d);
  };
  const pause = () => { playing = false; play.innerHTML = '&#9654;&#9654;'; };
  play.onclick = () => { playing = !playing; play.innerHTML = playing ? '&#10073;&#10073;' : '&#9654;&#9654;'; show(i); };
  p.querySelector('.prev').onclick = () => { pause(); show(i - 1); };
  p.querySelector('.next').onclick = () => { pause(); show(i + 1); };
  chips.forEach(c => c.onclick = () => { pause(); show(+c.dataset.at); });
  show(0);
}
"""


def render_html(report: dict, analysis: dict, thinking: dict | None, n: int = 3, line_for=None) -> str:
    """`line_for(fen, uci) -> SAN line` (optional) plays out drawn moves the engine had no line for."""
    g = report["game"]
    me = chess.WHITE if g["my_color"] == "white" else chess.BLACK
    dots = "." if me == chess.WHITE else "..."
    windows = {w["ply"]: w for w in (thinking or {}).get("windows", [])}
    s = report["summary"]
    sections = "".join(
        moment_html(i, c, windows.get(c["move"]["ply"]), me, _last_move(windows, c["move"]["ply"], c["record"]),
                    dots, line_for)
        for i, c in enumerate(pick(report, analysis, n), 1)
    ) or "<p>No critical moments: no costly moves and no sharp positions.</p>"
    return f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Critical moments {html.escape(g['id'])}</title>{THEME_HEAD}<style>{CSS}</style></head><body>{THEME_BUTTON}
<h1>Critical moments</h1>
<p class="sub">{html.escape(g['white'])} vs {html.escape(g['black'])} &middot; {html.escape(g['result'])} &middot;
you played {g['my_color']} &middot; WP lost {pct(s['wp_lost_total'])} in total</p>
{LEGEND}
{sections}
<script>{JS}</script>
</body></html>"""
