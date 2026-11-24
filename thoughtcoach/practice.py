"""Practice from your own games: positions where your thinking slipped, turned into questions.

Play -> review -> practise -> measure: each position comes from one of your rapid or classical games, at a
move where the review saw a gap, and asks the question that would have caught it:

  threat     their threat went unstopped              "What does White threaten?"
  dropped    you drew the best move, then played another  "You had the right idea here. Which move holds?"
  candidates the good move wasn't in your recorded thinking "Find three candidate moves before you look."
  piece      the best move improved your most passive piece "Which piece is doing least, and where should it go?"

Positions for your current focus come first. Whether practising helps is measured where it shows: in your
next games' rates (habits.py, the focus check), never claimed from the practice itself.
"""

from __future__ import annotations

import html

import chess
import chess.svg

from .fmt import game_url, pct

MIN_LOSS = 0.05  # the move cost at least this much win chance
PER_GAME = 2
# which kinds serve which focus (story.FOCUS keys)
FOR_FOCUS = {"q3": "threat", "ahead": "threat", "sharp": "threat", "check": "threat",
             "trust": "dropped", "candidates": "candidates", "q2": "piece"}


def _calm(m: dict, tt: float) -> bool:
    return not m.get("skipped") and (m.get("clock_before") is None or m["clock_before"] >= tt)


def positions(games: list[dict], focus: str | None = None, limit: int = 6) -> list[dict]:
    """Practice positions from `games` (profile.load entries, oldest first), newest games first."""
    found = []
    for g in reversed(games):
        r = g["report"]
        tt = r.get("thresholds", {}).get("time_trouble", 30)
        white = r["game"].get("my_color") == "white"
        them = "Black" if white else "White"
        mine = []
        for m in r["moves"]:
            if not _calm(m, tt) or (m.get("wp_loss") or 0) < MIN_LOSS or not m.get("fen"):
                continue
            q3, q2, best = m.get("q3") or {}, (m.get("q2") or {}).get("worst") or {}, m.get("best_move_san")
            base = {"game": g["id"], "fen": m["fen"], "white": white, "ply": m.get("ply"), "move": m.get("move_number"),
                    "played": m.get("move_san"), "loss": m["wp_loss"], "before": m.get("wp_before"), "after": m.get("wp_after")}
            if q3.get("status") == "not_drawn" and q3.get("threat_san"):
                mine.append({**base, "kind": "threat", "q": f"It's your move. What does {them} threaten?",
                             "a": f"{q3['threat_san']}, worth {pct(q3.get('swing') or 0)} to them. You played {m['move_san']}, "
                                  f"which didn't stop it."})
            elif m.get("gap") == "wrongly_rejected" and best:
                mine.append({**base, "kind": "dropped", "q": "You drew the right move here, then played something else. Which move holds?",
                             "a": f"{best}, the one you drew. {m['move_san']} took your win chance from {pct(m.get('wp_before') or 0)} "
                                  f"to {pct(m.get('wp_after') or 0)}."})
            elif q2 and not q2.get("you") and q2.get("engine_move") and best and q2["engine_move"].rstrip("+#") == best.rstrip("+#"):
                mine.append({**base, "kind": "piece", "q": "Which of your pieces is doing least, and where does it want to go?",
                             "a": f"The {q2.get('piece')} on {q2.get('square')}: {best} brings it into play. You played {m['move_san']}."})
            elif m.get("gap") in ("missed_candidate", "no_thoughts") and best:
                others = [s for s in (m.get("q4") or {}).get("engine", []) if s != best][:2]
                mine.append({**base, "kind": "candidates", "q": "Find three candidate moves before you choose one.",
                             "a": f"The engine's best was {best}" + (f" (also good: {', '.join(others)})" if others else "")
                                  + f". You played {m['move_san']}."})
        mine.sort(key=lambda p: -p["loss"])
        found += mine[:PER_GAME]
    # Up to half from the current focus, then the other kinds in turn (newest games first within each),
    # so one kind doesn't fill the page.
    want = FOR_FOCUS.get(focus or "")
    by: dict[str, list[dict]] = {}
    for p in found:
        by.setdefault(p["kind"], []).append(p)
    out = by.pop(want, [])[: (limit + 1) // 2] if want else []
    queues = list(by.values())
    while len(out) < limit and any(queues):
        for q in queues:
            if q and len(out) < limit:
                out.append(q.pop(0))
    return out


def _board(p: dict) -> str:
    board = chess.Board(p["fen"])
    return chess.svg.board(board, orientation=chess.WHITE if p["white"] else chess.BLACK, size=240, coordinates=True)


LABEL = {"threat": "Their idea", "dropped": "Trust your candidate", "candidates": "Candidates", "piece": "Worst piece"}


def section(games: list[dict], focus: str | None = None) -> str:
    ps = positions(games, focus)
    if not ps:
        return ("<section class='practice'><h2>Practice</h2><p class='cap'>Positions to practise come from your own rapid "
                "and classical games, where your thinking slipped. None yet: they appear after your first few reviews.</p></section>")
    cards = []
    for p in ps:
        fen_url = p["fen"].replace(" ", "_")
        side = "white" if p["white"] else "black"
        cards.append(
            f"<div class='pq'><span class='pk'>{html.escape(LABEL[p['kind']])}</span>{_board(p)}"
            f"<p class='pqq'>{html.escape(p['q'])}</p>"
            f"<details data-kind='{p['kind']}'><summary>Show the answer</summary><p>{html.escape(p['a'])}</p>"
            f"<p class='pl'><a href='https://lichess.org/analysis/standard/{html.escape(fen_url)}?color={side}' target='_blank' "
            f"rel='noopener'>Analyse on lichess</a> · <a href='{html.escape(game_url(p['game']))}'>From this game, move {p['move']}</a></p>"
            f"</details></div>")
    return (f"<section class='practice'><h2>Practice from your own games</h2><p class='cap'>Positions where your thinking "
            f"slipped, as the question that would have caught it{' (your current focus first)' if FOR_FOCUS.get(focus or '') else ''}. "
            f"Think before you open the answer. Whether it helps shows in your next games, under Your progress.</p>"
            f"<div class='pgrid'>{''.join(cards)}</div></section>"
            "<script>document.querySelectorAll('.practice details').forEach(d=>d.addEventListener('toggle',()=>{"
            "if(d.open)try{navigator.sendBeacon('/api/event','practice_reveal')}catch(e){}},{once:true}));</script>")


CSS = """
.practice .pgrid { display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:12px; }
.practice .pq { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px; }
.practice .pq svg { width:100%; height:auto; display:block; border-radius:6px; }
.practice .pk { display:inline-block; font-size:11.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
  color:var(--muted); margin-bottom:8px; }
.practice .pqq { font-weight:600; margin:10px 0 6px; }
.practice details summary { cursor:pointer; color:var(--accent, #3987e5); font-weight:600; }
.practice details p { margin:6px 0 0; color:var(--fg2); } .practice .pl { font-size:13px; }
"""
