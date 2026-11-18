"""Aagaard's questions across all games: how often you saw each one, what that depends on, and
real positions from your games (seen and missed) to look at.

  1. Where are the weaknesses?       (structural weaknesses the engine's line went for; aagaard.py)
  2. Which is your worst piece?      (your least active piece; aagaard.py)
  3. What does the opponent want?    (real threats: what they would play if it were their move)
  4. The best move                   (was the engine's best move among your candidates)

Everything is read from saved reviews: no engine work here. Moves in time trouble are left out
of 3 and 4 (as in the rest of the review), and games without any thoughts recorded are skipped.
"""

from __future__ import annotations

import html

import chess
import chess.svg

from .fmt import game_url, pct

BOARD = 168
SEEN, MISSED = "#2a78d6", "#eb6834"
EXAMPLES = 3  # per question on the home page (a game's dashboard shows more): worst misses first, then seen


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _rows(games: list[dict]):
    """(game, move) for every reviewed move outside time trouble, in games with thoughts."""
    for g in games:
        r = g["report"]
        if not r["summary"].get("moves_with_thoughts"):
            continue
        tt = r["thresholds"].get("time_trouble", 30)
        for m in r["moves"]:
            if m.get("skipped") or (m.get("clock_before") is not None and m["clock_before"] < tt):
                continue
            yield g, m


def _me(g: dict) -> chess.Color:
    return chess.WHITE if g["report"]["game"]["my_color"] == "white" else chess.BLACK


def _fen_at(g: dict, move_no: int) -> tuple[str | None, int | None]:
    """The position where you were to play your move `move_no`, and that move's ply."""
    me = _me(g)
    for m in g["report"]["moves"]:
        if m["move_number"] == move_no and m.get("fen") and chess.Board(m["fen"]).turn == me:
            return m["fen"], m.get("ply")
    return None, None


def _arrow(board: chess.Board, san: str | None, color: str, theirs: bool = False):
    if not san:
        return []
    b = board.copy()
    if theirs:
        b.push(chess.Move.null())
    try:
        mv = b.parse_san(san)
    except ValueError:
        return []
    return [chess.svg.Arrow(mv.from_square, mv.to_square, color=color + "cc")]


def _example(g, move_no, fen, seen, title, note, fill=(), arrows=(), ply=None):
    return {"game": g["id"], "date": g["report"]["game"].get("date"), "move": move_no, "fen": fen, "ply": ply,
            "seen": seen, "title": title, "note": note, "fill": list(fill), "arrows": list(arrows),
            "me": _me(g)}


def _pick(seen: list, missed: list, n: int) -> list:
    """The worst misses first (lists come sorted), then about a third seen, n in all."""
    out = missed[: n - min(len(seen), max(1, n // 3))]
    out += seen[: n - len(out)]
    return out


# ---------------------------------------------------------------- the four questions


def weaknesses(games: list[dict]) -> dict:
    split = {"theirs": [0, 0], "yours": [0, 0]}
    kinds: dict[str, list[int]] = {}
    made = [0, 0]  # weaknesses your own move created
    seen, missed = [], []
    for g in reversed(games):  # newest games first in the examples
        for w in g["report"]["summary"].get("q1_list") or []:
            hit = bool(w.get("you"))
            split[w["side"]][0] += hit
            split[w["side"]][1] += 1
            k = "hole" if w["kind"] == "hole" else "loose piece" if w["kind"].startswith("loose") else "weak pawn"
            kinds.setdefault(k, [0, 0])
            kinds[k][0] += hit
            kinds[k][1] += 1
            if w.get("created_by"):
                made[0] += hit
                made[1] += 1
            fen, ply = _fen_at(g, w["move"])
            if not fen:
                continue
            whose = "their" if w["side"] == "theirs" else "your"
            if w.get("created_by"):  # show the position after the move that created it
                board = chess.Board(fen)
                ex = _example(g, w["move"], w.get("fen_after") or fen, hit,
                              f"{w['created_by']} left your {w['kind'].replace('loose ', '') + ' loose' if w['kind'].startswith('loose') else w['kind']} on {w['square']}",
                              "You saw it coming" if hit else "Created by your own move; the engine's reply goes for it",
                              fill=[(chess.parse_square(w["square"]), SEEN if hit else MISSED)],
                              arrows=_arrow(board, w["created_by"], "#1f4fa8"), ply=(ply or 0) + 1)
                (seen if hit else missed).insert(0, ex)  # the costliest kind: show first
                continue
            how = {"marked": "you marked it", "your arrow": "your arrow went there",
                   "their arrow": "you drew their move to it"}.get(w.get("you"), "")
            note = (f"{how.capitalize()}" + (f" (move {w['you_move']})" if w.get("you_move") else "") if hit
                    else "Not marked; the engine's line went for it")
            sq = chess.parse_square(w["square"])
            ex = _example(g, w["move"], fen, hit, f"{whose.capitalize()} {w['kind']} on {w['square']}", note,
                          fill=[(sq, SEEN if hit else MISSED)], ply=ply)
            (seen if hit else missed).append(ex)
    return {"found": sum(v[0] for v in split.values()), "total": sum(v[1] for v in split.values()),
            "split": [("Their weaknesses", *split["theirs"]), ("Your own", *split["yours"])]
            + ([("Created by your own move", *made)] if made[1] else [])
            + [(k.capitalize() + "s", *v) for k, v in sorted(kinds.items())],
            "seen_all": seen, "missed_all": missed}


def worst_piece(games: list[dict]) -> dict:
    by_piece: dict[str, list[int]] = {}
    seen, missed = [], []
    for g in reversed(games):
        for w in g["report"]["summary"].get("q2_list") or []:
            hit = bool(w.get("you"))
            by_piece.setdefault(w["piece"], [0, 0])
            by_piece[w["piece"]][0] += hit
            by_piece[w["piece"]][1] += 1
            fen, ply = _fen_at(g, w["move"])
            if not fen:
                continue
            how = {"named": "You named it", "moved it in a candidate": "You considered a move with it"}.get(w.get("you"), "")
            plan = f"; engine improves it with {w['engine_move']}" if w.get("engine_move") else ""
            note = (how if hit else "Not mentioned or moved in a candidate") + plan
            ex = _example(g, w["move"], fen, hit, f"{w['piece'].capitalize()} {w['square']}: {w['activity'] * 100:.0f}% of its squares",
                          note, fill=[(chess.parse_square(w["square"]), SEEN if hit else MISSED)], ply=ply)
            ex["passive"] = w["activity"]
            (seen if hit else missed).append(ex)
    missed.sort(key=lambda x: x["passive"])
    return {"found": sum(v[0] for v in by_piece.values()), "total": sum(v[1] for v in by_piece.values()),
            "split": [(k.capitalize() + "s", *v) for k, v in sorted(by_piece.items(), key=lambda kv: -kv[1][1])],
            "seen_all": seen, "missed_all": missed}


def threats(games: list[dict]) -> dict:
    counts = {"spotted": 0, "handled": 0, "not_drawn": 0}
    seen, missed = [], []
    for g, m in _rows(games):
        q3 = m.get("q3") or {}
        if q3.get("status") not in counts:
            continue
        counts[q3["status"]] += 1
        if q3["status"] == "handled":
            continue
        board = chess.Board(m["fen"])
        hit = q3["status"] == "spotted"
        note = ("You drew it before it could happen" if hit else
                f"Not spotted, and {m['move_san']} didn't stop it ({pct(-m['wp_loss'])})")
        ex = _example(g, m["move_number"], m["fen"], hit,
                      f"Threat {q3['threat_san']}: worth {pct(q3['swing'])} to them", note,
                      arrows=_arrow(board, q3["threat_san"], "#c0392b", theirs=True), ply=m.get("ply"))
        ex["size"] = q3["swing"]
        (seen if hit else missed).append(ex)
    seen.sort(key=lambda x: -x["size"])
    missed.sort(key=lambda x: -x["size"])
    total = sum(counts.values())
    # The question is whether you saw it: threats your move happened to stop are shown, not counted.
    return {"found": counts["spotted"], "total": total, "found_label": "spotted",
            "meter": [(counts["spotted"], "var(--s1)"), (counts["handled"], "var(--s1-light)"), (counts["not_drawn"], "var(--s2)")],
            "split": [("Spotted", counts["spotted"], total), ("Stopped by your move", counts["handled"], total),
                      ("Missed", counts["not_drawn"], total)],
            "seen_all": seen, "missed_all": missed}


def best_move(games: list[dict]) -> dict:
    found = total = rejected = never = 0
    seen, missed = [], []
    for g, m in _rows(games):
        cands = {d["move_san"] for d in m.get("drawn_candidates", []) if d.get("legal") and d.get("move_san")}
        if not cands or not m.get("best_move_san") or not m.get("fen"):
            continue
        total += 1
        best = m["best_move_san"]
        considered = best in cands or best == m["move_san"]
        found += considered
        board = chess.Board(m["fen"])
        arrows = _arrow(board, best, "#15781b")
        if m["move_san"] != best:
            arrows += _arrow(board, m["move_san"], "#1f4fa8")
        if m.get("mistake"):
            if considered:
                rejected += 1
            else:
                never += 1
            note = (f"You considered {best}, then played {m['move_san']}" if considered
                    else f"Not among your candidates ({', '.join(sorted(cands)[:3])})")
            missed.append(_example(g, m["move_number"], m["fen"], False,
                                   f"Best {best}; {m['move_san']} cost {pct(m['wp_loss'])}", note, arrows=arrows,
                                   ply=m.get("ply")))
            missed[-1]["size"] = m["wp_loss"]
        elif considered and m["move_san"] == best and len((m.get("q4") or {}).get("engine") or []) == 1:
            seen.append(_example(g, m["move_number"], m["fen"], True, f"{best}: the only good move",
                                 "You found it and played it", arrows=arrows, ply=m.get("ply")))
    missed.sort(key=lambda x: -x["size"])
    return {"found": found, "total": total, "found_label": "considered",
            "split": [("Costly: best considered, not played", rejected, rejected + never),
                      ("Costly: best never considered", never, rejected + never)],
            "seen_all": seen[::-1], "missed_all": missed}


QUESTIONS = [
    ("q1", "Where are the weaknesses?", "Weak pawns, holes and loose pieces, on both sides, that the engine's line used",
     weaknesses),
    ("q2", "Which is your worst piece?", "Your least active piece, when it was clearly passive", worst_piece),
    ("q3", "What does your opponent want?", "Real threats: what they'd play if it were their move", threats),
    ("q4", "And the best move?", "Was the engine's best move among the moves you considered", best_move),
]


def compute(games: list[dict], examples: int = EXAMPLES) -> list[dict]:
    out = []
    for k, q, what, fn in QUESTIONS:
        r = fn(games)
        r["examples"] = _pick(r.pop("seen_all"), r.pop("missed_all"), examples)
        out.append({"key": k, "q": q, "what": what, **r})
    return out


# ---------------------------------------------------------------- page


def _board(ex: dict, link: str = "game") -> str:
    board = chess.Board(ex["fen"])
    svg = chess.svg.board(board, orientation=ex["me"], size=BOARD, coordinates=False,
                          fill={sq: c + "ee" for sq, c in ex["fill"]}, arrows=ex["arrows"])
    tag = "seen" if ex["seen"] else "missed"
    if link == "lichess":  # this position on lichess (the ply before your move)
        href, tip = f"https://lichess.org/{ex['game']}#{max((ex['ply'] or 1) - 1, 0)}", "Open this position on lichess"
    else:
        href, tip = game_url(ex['game']), "Open this game"
    return (f"<a class='qx {tag}' href='{_e(href)}' title='{tip}' target='{'_blank' if link == 'lichess' else '_self'}'>{svg}"
            f"<span class='qt'><b>{_e(ex['title'])}</b><span>{_e(ex['note'])}</span>"
            f"<small><i>{'Seen' if ex['seen'] else 'Missed'}</i> move {ex['move']} &middot; {_e(ex['date'] or ex['game'])}</small></span></a>")


def _split(rows) -> str:
    out = []
    for label, n, of in rows:
        if not of:
            continue
        out.append(f"<div class='qrow'><span>{_e(label)}</span><span class='trk'><span style='width:{n / of * 100:.1f}%'></span></span>"
                   f"<b>{n}/{of}</b></div>")
    return "".join(out)


def render(qs: list[dict], title: str = "Aagaard's questions", link: str = "game",
           sub: str = "The questions to ask in every position, and how often your arrows and words answered them. "
                      "Click a board to open the game.") -> str:
    if not any(q["total"] for q in qs):
        return ""
    panels = []
    for i, q in enumerate(qs, 1):
        rate = q["found"] / q["total"] if q["total"] else 0
        examples = "".join(_board(x, link) for x in q["examples"]) or "<p class='qwhat'>No examples yet.</p>"
        segs = q.get("meter") or [(q["found"], "var(--s1)"), (q["total"] - q["found"], "var(--s2)")]
        meter = "".join(f"<span style='width:{n / q['total'] * 100:.2f}%;background:{c}'></span>" for n, c in segs if n and q["total"])
        panels.append(f"""<div class="qpanel">
  <div class="qhead"><span class="qnum">{i}</span><div><h3>{_e(q['q'])}</h3><p class="qwhat">{_e(q['what'])}</p></div>
    <div class="qrate"><b>{pct(rate) if q['total'] else '–'}</b><small>{q['found']} of {q['total']} {q.get('found_label', 'seen')}</small></div></div>
  <div class="qmeter">{meter}</div>
  <div class="qsplit">{_split(q['split'])}</div>
  <div class="qxs">{examples}</div>
</div>""")
    return f"""<section class="aagaard" id="aagaard">
<h2>{_e(title)}</h2>
<p class="sub">{_e(sub)}
<span class="qkey"><i style="background:{SEEN}"></i>seen <i style="background:{MISSED}"></i>missed
<i style="background:#c0392b"></i>their threat <i style="background:#15781b"></i>best move <i style="background:#1f4fa8"></i>your move</span></p>
<div class="qgrid">{''.join(panels)}</div>
</section>"""


CSS = """
.aagaard { margin:22px 0 6px; } .aagaard h2 { font-size:18px; margin:0 0 4px; }
.aagaard .sub { color:var(--fg2); font-size:13px; margin:0 0 12px; }
.qkey { display:inline-flex; gap:5px; align-items:center; flex-wrap:wrap; margin-left:8px; color:var(--muted); font-size:12px; }
.qkey i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-left:6px; }
.qgrid { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 500px), 1fr)); gap:14px; }
.qpanel { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; min-width:0; }
.qhead { display:grid; grid-template-columns:auto 1fr auto; gap:12px; align-items:start; }
.qhead h3 { font-size:16px; margin:0; } .qhead .qwhat { margin:2px 0 0; color:var(--muted); font-size:12.5px; }
.qnum { width:28px; height:28px; border-radius:50%; background:var(--s1); color:#fff; display:grid; place-items:center; font-weight:700; }
.qrate { text-align:right; } .qrate b { font-size:26px; line-height:1; display:block; font-variant-numeric:tabular-nums; }
.qrate small { color:var(--muted); font-size:12px; white-space:nowrap; }
.qmeter { height:10px; background:var(--s2); border-radius:4px; overflow:hidden; margin:12px 0 10px; display:flex; }
.qmeter span { border-right:2px solid var(--card); } .qmeter span:last-child { border-right:0; }
.qsplit { margin-bottom:12px; }
.qrow { display:grid; grid-template-columns:minmax(0, 210px) 1fr 52px; gap:8px; align-items:center; font-size:12.5px; margin:4px 0; color:var(--fg2); }
.qrow > span:first-child { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.qrow .trk { height:8px; background:var(--s1-soft); border-radius:4px; overflow:hidden; display:flex; }
.qrow .trk span { background:var(--s1); } .qrow b { text-align:right; font-variant-numeric:tabular-nums; color:var(--fg); font-weight:600; }
.qxs { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }
.qx { display:flex; flex-direction:column; gap:6px; text-decoration:none; color:var(--fg); border-radius:8px; padding:6px;
  border:1px solid var(--line); border-top:3px solid var(--s2); }
.qx.seen { border-top-color:var(--s1); } .qx:hover { border-color:var(--fg2); }
.qx svg { width:100%; height:auto; display:block; border-radius:4px; }
.qt { display:flex; flex-direction:column; gap:2px; font-size:12px; line-height:1.35; }
.qt span { color:var(--fg2); } .qt small { color:var(--muted); font-size:11px; }
.qt small i { font-style:normal; font-weight:700; color:var(--s2); } .qx.seen .qt small i { color:var(--s1); }
"""
