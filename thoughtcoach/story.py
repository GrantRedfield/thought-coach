"""Your game as a story: a short run of full-screen cards, like a year-in-review, that you step
through. Only the moments that stand out in this game make it in, in the order they happened,
and it ends with one thing to focus on next game.

Every possible moment is a "beat" with a score (roughly: how much win chance it involved, or how
telling it is about your thinking). The best beats are kept, at most one per move and one per
kind, MAX_BEATS in all. Beats come from:

  the turning point       your costliest move: what you considered, what the engine wanted
  trust your candidates   a costly move where you had the good move and played another
  their idea (Q3)         a real threat you didn't see (or a big one you did)
  weaknesses (Q1)         one your own move created, one of theirs you found, or one you missed
  worst piece (Q2)        your piece that was stuck, or the one you named
  your thoughts           the insights (called it, read their plan, reactions...) with quotes
  first strike            who broke the game open, or the blunder that decided it
  the clock               when time trouble took over

The focus for next game is the cause that cost the most win chance in this game, nudged
towards causes that keep coming back across your games (`history`, from profile.py).
Everything is read from the saved review: no engine work.
"""

from __future__ import annotations

import html
import json
import re

import chess
import chess.pgn
import chess.svg

from .critical import _said
from .gametype import classify
from .strike import EXPLAIN as STRIKE_EXPLAIN
from . import ahead, bayes, masters, studies, timing, tone
from .fmt import move_went, went, game_url, home_url, pct

MAX_BEATS = 6
MIN_SCORE = 0.3  # a quiet game gets a shorter story rather than filler
RELATIVE = 0.33  # a card needs at least this share of the top moment's weight
DECISIVE_LOSS, DECISIVE_SHARE = 0.25, 0.6  # one move this costly, and this share of all you lost: it decided the game
DECISIVE_BEATS = 2  # then the story is that moment and at most one other
VOICE_KINDS = {"rosy", "worry_held", "gloomy", "gave_up", "inner", "tide"}  # moments your voice tells
BLUE, DARK_BLUE, GREEN, RED, ORANGE, PURPLE = "#3c78d8", "#1f4fa8", "#15781b", "#c0392b", "#eb6834", "#7b2cbf"

ACCENT = {  # card background accents, by kind
    "intro": "#2a78d6", "masters": "#5f3dc4", "turning": "#d9480f", "trust": "#e8590c", "threat": "#c92a2a", "threat_seen": "#2b8a3e",
    "weak_made": "#e67700", "weak_found": "#2b8a3e", "weak_missed": "#e67700", "worst": "#7048e8",
    "worst_named": "#2b8a3e", "insight": "#0c8599", "strike": "#f08c00", "decided": "#c92a2a", "clock": "#495057",
    "result": "#343a40", "focus": "#2f9e44", "tide": "#1098ad", "gave_up": "#862e9c", "instinct": "#1c7ed6",
    "inner": "#5f3dc4", "voice": "#0b7285", "truths": "#5c7cfa", "caught": "#e8590c", "rosy": "#d9480f", "worry_held": "#e67700", "gloomy": "#1971c2",
}
MOOD_COLOR = {"upbeat": "#51cf66", "worried": "#ff922b", "neutral": "#ffffffaa"}
MOOD_WORD = {"upbeat": "upbeat", "worried": "worried", "neutral": "calculating", "quiet": "quiet"}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _label(ply: int, san: str) -> str:
    n = (ply + 1) // 2
    return f"{n}. {san}" if ply % 2 else f"{n}... {san}"


def _short(text: str, n: int = 150) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


class Ctx:
    def __init__(self, report: dict, analysis: dict, thinking: dict | None, game: chess.pgn.Game | None):
        self.report, self.analysis, self.game = report, analysis, game
        g = report["game"]
        self.me = chess.WHITE if g["my_color"] == "white" else chess.BLACK
        self.moves = [m for m in report["moves"] if not m.get("skipped")]
        self.by_ply = {m["ply"]: m for m in self.moves}
        self.windows = {w["ply"]: w for w in (thinking or {}).get("windows", [])}
        self.timing = timing.game(analysis, report) or {"rushed": [], "lingered": [], "usual": 0}
        self.tone = {r["ply"]: r for r in (report.get("tone") or {}).get("moves", [])}
        self.caught = ahead.caught(analysis, report)  # costly moves while you were winning
        self.ahead_note = None  # the cross-game pattern, set by build() from history
        self.history = None  # set by build()
        self.white = report["game"]["my_color"] == "white"
        tt = report["thresholds"].get("time_trouble", 30)
        self.tt = tt
        self.in_tt = lambda m: m.get("clock_before") is not None and m["clock_before"] < tt  # noqa: E731
        self.boards, self.played = [], []
        if game is not None:
            self.played = list(game.mainline_moves())
            b = game.board()
            self.boards.append(b.copy())
            for mv in game.mainline_moves():
                b.push(mv)
                self.boards.append(b.copy())

    def quote(self, ply: int) -> str | None:
        said = [u for u in _said(self.windows.get(ply)) if len(u.split()) >= 3]
        return _short(" … ".join(said[:2]), 140) if said else None

    def board_before(self, ply: int) -> chess.Board | None:
        if 0 < ply <= len(self.boards):
            return self.boards[ply - 1]
        m = self.by_ply.get(ply)
        return chess.Board(m["fen"]) if m else None


def _arrow(board: chess.Board, san: str | None, color: str, theirs: bool = False):
    if not san:
        return None
    b = board.copy(stack=False)
    if theirs:  # their move in a position where it's your turn: as if you passed
        b.push(chess.Move.null())
    try:
        mv = b.parse_san(san)
    except ValueError:
        return None
    return (mv.from_square, mv.to_square, color)


def _beat(kind, ply, score, eyebrow, title, body, quote=None, board=None, arrows=(), fill=None, stat=None, frames=None):
    return {"kind": kind, "ply": ply, "score": score, "eyebrow": eyebrow, "title": title, "body": body,
            "quote": quote, "fen": board.fen() if board is not None else None,
            "arrows": [a for a in arrows if a], "fill": fill or {}, "stat": stat, "frames": frames or []}


MOVE_LABEL = re.compile(r"\b(\d+)\.(\.\.)?\s?([KQRBNO][^\s,;:()]*|[a-h][^\s,;:()]*)")
MAX_ANIMATED = 4


def mentioned_moves(c: Ctx, text: str) -> list[int]:
    """Plies of the game's moves named in `text` ('8. Bxc4', '15... f5'), checked against the moves played."""
    plies = []
    for n, dots, san in MOVE_LABEL.findall(text):
        ply = 2 * int(n) - (0 if dots else 1)
        if 0 < ply <= len(c.played) and ply not in plies:
            if c.boards[ply - 1].san(c.played[ply - 1]).rstrip("+#") == san.rstrip("+#.!?"):
                plies.append(ply)
    return sorted(plies[:MAX_ANIMATED])  # in game order


def move_frames(c: Ctx, plies: list[int], predicted: bool) -> list[dict]:
    """Two frames per move: the arrow (what you predicted, or the move), then the move made."""
    frames = []
    for ply in plies:
        mv, before, after = c.played[ply - 1], c.boards[ply - 1], c.boards[ply]
        label = _label(ply, before.san(mv))
        frames.append({"fen": before.fen(), "arrow": (mv.from_square, mv.to_square, BLUE if predicted else ORANGE),
                       "last": None, "cap": f"{label}: you saw it coming" if predicted else label})
        frames.append({"fen": after.fen(), "arrow": None, "last": (mv.from_square, mv.to_square),
                       "cap": f"{label}: and it was played" if predicted else f"{label} on the board"})
    return frames


# ---------------------------------------------------------------- beats


def _rushed_note(c: Ctx, ply: int) -> str:
    """' Only Nb6 held, and you took 12 s (your usual think: 21 s). Earlier you spent 64 s on 20... Ne6,
    where three moves were about equal.' for a critical position you rushed, else ''."""
    p = next((p for p in c.timing["rushed"] if p["ply"] == ply), None)
    if not p:
        return ""
    note = f" Only {p['best']} held, and you took {p['time']:.0f} s (your usual think: {c.timing['usual']:.0f} s)."
    long = next((q for q in c.timing["lingered"] if q["ply"] < ply), None)
    if long:
        note += (f" Earlier you spent {long['time']:.0f} s on {timing.label(long, c.white)}, where {long['good']} moves "
                 f"were about equally good.")
    return note


def turning_point(c: Ctx) -> list[dict]:
    costly = [m for m in c.moves if m["mistake"] and not c.in_tt(m)]
    if not costly:
        return []
    m = max(costly, key=lambda m: m["wp_loss"])
    board = chess.Board(m["fen"])
    cands = [d["move_san"] for d in m.get("drawn_candidates", []) if d.get("legal") and d.get("move_san")]
    best = m.get("best_move_san")
    if best and best in cands and best != m["move_san"]:
        why = f"{best} was on your list. You played {m['move_san']} instead."
    elif cands:
        others = [x for x in cands if x != m["move_san"]]
        why = (f"You drew or said {', '.join(others[:3])}. " if others else "It was the only move you drew or said. ") + \
              (f"{best} isn't in your recorded thinking." if best else "")
    else:
        why = f"No candidates shown. {best} was the move." if best else "No candidates shown."
    seen = reply_seen(c, m)
    if seen:
        why += f" You'd drawn their {seen} yourself, and it came next."
    made = [w for w in (m.get("q1") or {}).get("weaknesses", []) if w.get("created_by")]
    if made:  # the same move also created a weakness (Aagaard's question 1)
        w = made[0]
        kind = w["kind"].replace("loose ", "") + " loose" if w["kind"].startswith("loose") else w["kind"]
        why += f" It also left your {kind} on {w['square']}."
    why += _rushed_note(c, m["ply"])
    arrows = [_arrow(board, x, BLUE) for x in cands if x not in (m["move_san"], best)][:3]
    arrows += [_arrow(board, m["move_san"], DARK_BLUE), _arrow(board, best, GREEN)]
    return [_beat("turning", m["ply"], m["wp_loss"] * 3 + 0.3, f"The turning point · move {m['move_number']}",
                  f"{_label(m['ply'], m['move_san'])}", f"Your win chance went from {pct(m['wp_before'])} to "
                  f"{pct(m['wp_after'])}. {why}", c.quote(m["ply"]), board, arrows,
                  stat=move_went(m))]


def rushed(c: Ctx) -> list[dict]:
    """Critical positions you played fast and lost in (timing.py), other than the turning point,
    which already says so."""
    costly = [m for m in c.moves if m["mistake"] and not c.in_tt(m)]
    turning = max(costly, key=lambda m: m["wp_loss"])["ply"] if costly else None
    out = []
    for p in c.timing["rushed"]:
        if p["ply"] == turning or p["ply"] not in c.by_ply:
            continue
        m = c.by_ply[p["ply"]]
        board = chess.Board(m["fen"])
        out.append(_beat("rushed", p["ply"], p["wp_loss"] * 2.6, f"Time where it mattered · move {p['move_number']}",
                         f"One move held, and you took {p['time']:.0f} s",
                         f"{_label(m['ply'], m['move_san'])} took your win chance from {pct(m['wp_before'])} to "
                         f"{pct(m['wp_after'])}.{_rushed_note(c, p['ply'])}", c.quote(m["ply"]), board,
                         [_arrow(board, m["move_san"], DARK_BLUE), _arrow(board, p["best"], GREEN)],
                         stat=f"{p['time']:.0f} s"))
    return out


def moods(c: Ctx) -> list[dict]:
    """What your tone said against what the board said (tone.py): sounding good just before it went
    wrong, worry when the engine wanted you to attack, and worry while you were doing well."""
    out = []
    rows = [r for r in c.tone.values() if not r["tt"]]
    for r in rows:
        nxt = [x for x in rows if r["ply"] <= x["ply"] <= r["ply"] + 4 and x["wp_loss"] >= 0.10]
        if r["mood"] == "upbeat" and r["quote"] and nxt:
            m = nxt[0]
            later = "Then" if m["ply"] == r["ply"] else f"{(m['ply'] - r['ply']) // 2} move{'s' * (m['ply'] - r['ply'] > 2)} later"
            out.append(_beat("rosy", r["ply"], m["wp_loss"] * 2.4, f"Your voice · move {r['move_number']}",
                             "You sounded good about it",
                             f"Around move {r['move_number']} you said “{r['quote']}”, with your win chance at "
                             f"{pct(r['wp_before'])}. {later}, {_label(m['ply'], m['move_san'])} took it from "
                             f"{pct(m['wp_before'])} to {pct(m['wp_after'])}. Feeling good is fine; it is a good "
                             f"moment to look once more." + (f" Across your games, {c.ahead_note[0].lower()}{c.ahead_note[1:]}" if c.ahead_note else ""),
                             None, c.board_before(m["ply"])))
            break
    for r in rows:
        if r["mood"] == "worried" and r["best_forcing"] and not r["played_forcing"] and r["wp_loss"] >= 0.05 and r["quote"]:
            m = c.by_ply.get(r["ply"])
            board = chess.Board(m["fen"]) if m else None
            arrows = [_arrow(board, r["move_san"], DARK_BLUE), _arrow(board, r["best"], GREEN)] if board else []
            out.append(_beat("worry_held", r["ply"], r["wp_loss"] * 2.4, f"Your voice · move {r['move_number']}",
                             "Worry held you back", f"You said “{r['quote']}” and played {_label(r['ply'], r['move_san'])} "
                             f"({pct(r['wp_before'])} → {pct(r['wp_after'])}). The engine's move was the forcing "
                             f"{r['best']}.", None, board, arrows))
            break
    gloomy = [r for r in rows if r["mood"] == "worried" and r["quote"] and r["wp_before"] > tone.GLOOMY_ABOVE
              and r["wp_loss"] < 0.05]
    if gloomy:
        r = max(gloomy, key=lambda x: x["wp_before"])
        out.append(_beat("gloomy", r["ply"], 0.35, f"Your voice · move {r['move_number']}", "Better than it sounded",
                         f"You said “{r['quote']}” while the engine had you at {pct(r['wp_before'])}, and you kept it "
                         f"there ({pct(r['wp_before'])} → {pct(r['wp_after'])}).", None, c.board_before(r["ply"])))
    return out


def comfortable(c: Ctx) -> list[dict]:
    """A costly move while you were winning: how fast it was, and what came back (ahead.py)."""
    if not c.caught:
        return []
    m = c.caught[0]
    if m["ply"] not in c.by_ply:
        return []
    board = chess.Board(c.by_ply[m["ply"]]["fen"])
    usual = c.timing.get("usual") or 0
    pace = f", against your usual {usual:.0f} s" if usual else ""
    return [_beat("caught", m["ply"], m["wp_loss"] * 2.2, f"When you're ahead · move {m['move_number']}",
                  "Comfortable, then caught",
                  f"You were at {pct(m['wp_before'])} and played {_label(m['ply'], m['move_san'])} in {m['time']:.0f} s"
                  f"{pace}. {m['reply'] or 'Their reply'} took you to {pct(m['wp_after'])}.",
                  c.quote(m["ply"]), board, [_arrow(board, m["move_san"], DARK_BLUE),
                                             _arrow(board, c.by_ply[m['ply']].get('best_move_san'), GREEN)])]


def voice_card(c: Ctx) -> dict | None:
    """The voice, shown off: your mood move by move on the win-chance curve, what you sounded like
    before your costly moves, and a finding when this game has enough behind it."""
    rows = sorted(c.tone.values(), key=lambda r: r["ply"])
    talked = [r for r in rows if r["mood"] != "quiet"]
    if len(talked) < 3:
        return None
    counts = {k: sum(r["mood"] == k for r in rows) for k in ("upbeat", "worried", "neutral", "quiet")}
    costly = [r for r in rows if r["costly"] and not r["tt"]]
    before = {}
    for r in costly:
        before[r["mood"]] = before.get(r["mood"], 0) + 1
    line = None
    st = tone.stats(rows)
    how = {"upbeat": "sounding upbeat", "worried": "sounding worried", "neutral": "calculating", "quiet": "quiet"}
    if st["findings"]:
        line = st["findings"][0]
    elif len(costly) == 1:
        line = f"Before your costly move you were {how[costly[0]['mood']]}."
    elif costly:
        parts = [f"{n} {how[k]}" for k, n in sorted(before.items(), key=lambda kv: -kv[1])]
        line = f"Before your {len(costly)} costly moves: {', '.join(parts)}."
    # The quote: something said with a clear mood, soonest before the costliest move.
    worst = max(costly, key=lambda r: r["wp_loss"], default=None)
    toned = [r for r in rows if r["quote"] and r["mood"] in ("upbeat", "worried")]
    if worst and any(r["ply"] <= worst["ply"] for r in toned):
        key = max((r for r in toned if r["ply"] <= worst["ply"]), key=lambda r: r["ply"])
    else:
        key = max(toned or [r for r in rows if r["quote"]], key=lambda r: r["wp_loss"], default=None)
    return {"talked": len(talked), "of": len(rows), "counts": counts, "line": line,
            "marks": [(r["ply"], r["mood"], r["wp_after"]) for r in rows if r["mood"] != "quiet"],
            "quote": {"text": key["quote"], "move": _label(key["ply"], key["move_san"]), "mood": key["mood"]} if key else None}


def trust(c: Ctx) -> list[dict]:
    out = []
    for m in c.moves:
        rejected = (m.get("q4") or {}).get("wrongly_rejected") or []
        if not m["mistake"] or c.in_tt(m) or not rejected:
            continue
        board = chess.Board(m["fen"])
        out.append(_beat("trust", m["ply"], m["wp_loss"] * 2.5, f"Trust your candidates · move {m['move_number']}",
                         f"You had {rejected[0]}", f"You drew {rejected[0]}, then played {m['move_san']} "
                         f"({move_went(m)}). The first idea was the right one.", c.quote(m["ply"]), board,
                         [_arrow(board, rejected[0], GREEN), _arrow(board, m["move_san"], DARK_BLUE)]))
    return out


def threats(c: Ctx) -> list[dict]:
    out = []
    for m in c.moves:
        q3 = m.get("q3") or {}
        if c.in_tt(m) or not q3.get("threat_san"):
            continue
        board = chess.Board(m["fen"])
        arrow = _arrow(board, q3["threat_san"], RED, theirs=True)
        if q3.get("status") == "not_drawn" and q3["swing"] >= 0.1:
            out.append(_beat("threat", m["ply"], q3["swing"] * 2 + m["wp_loss"],
                             f"Question 1, their idea · move {m['move_number']}", "What did they want?",
                             f"{q3['threat_san']}, worth {pct(q3['swing'])} to them. You didn't draw it, and "
                             f"{m['move_san']} didn't stop it ({move_went(m)}).", c.quote(m["ply"]), board, [arrow]))
        elif q3.get("status") == "spotted" and q3["swing"] >= 0.2:
            out.append(_beat("threat_seen", m["ply"], q3["swing"] * 0.9,
                             f"Question 1, their idea · move {m['move_number']}", "You saw it coming",
                             f"You drew {q3['threat_san']} before it could happen. It was worth {pct(q3['swing'])} "
                             "to them.", c.quote(m["ply"]), board, [arrow]))
    return out


def weaknesses(c: Ctx) -> list[dict]:
    out = []
    for w in c.report["summary"].get("q1_list") or []:
        m = next((x for x in c.moves if x["move_number"] == w["move"]), None)
        if not m:
            continue
        sq = chess.parse_square(w["square"])
        kind = w["kind"].replace("loose ", "") + " loose" if w["kind"].startswith("loose") else w["kind"]
        if w.get("created_by"):
            board = chess.Board(w.get("fen_after") or m["fen"])
            why, frames = "", None
            out.append(_beat("weak_made", m["ply"], m["wp_loss"] * 2.5 + 0.2,
                             f"Question 2, weaknesses · move {m['move_number']}", "Your move made a weakness",
                             f"{w['created_by']} left your {kind} on {w['square']}, and the engine's reply goes "
                             "straight for it." + (" You'd pointed at it." if w.get("you") else ""),
                             c.quote(m["ply"]), board, fill={sq: ORANGE}))
        elif w.get("you") and w["side"] == "theirs":
            board = chess.Board(m["fen"])
            why, frames = _weak_line(board, w, sq, BLUE)
            when = f" at move {w['you_move']}" if w.get("you_move") else ""
            out.append(_beat("weak_found", m["ply"], 0.45, f"Question 2, weaknesses · move {m['move_number']}",
                             f"You found the {w['kind'].replace('loose ', 'loose ')}",
                             f"You pointed at {w['square']}{when}, and the engine's best line goes right for it: "
                             f"their {kind} on {w['square']}.{why}", c.quote(m["ply"]), board, fill={sq: BLUE},
                             frames=frames))
        elif not w.get("you") and 0.3 <= m["wp_before"] <= 0.7:  # a slow question: only while it's in the balance
            board = chess.Board(m["fen"])
            whose = "their" if w["side"] == "theirs" else "your"
            why, frames = _weak_line(board, w, sq, ORANGE)
            out.append(_beat("weak_missed", m["ply"], 0.3, f"Question 2, weaknesses · move {m['move_number']}",
                             "Where are the weaknesses?", f"{whose.capitalize()} {kind} on {w['square']}: the engine's "
                             f"line used it, and it doesn't appear in your arrows or words.{why}", None, board,
                             fill={sq: ORANGE}, frames=frames))
    return out


def worst_piece(c: Ctx) -> list[dict]:
    out = []
    for w in c.report["summary"].get("q2_list") or []:
        m = next((x for x in c.moves if x["move_number"] == w["move"]), None)
        if not m:
            continue
        board = chess.Board(m["fen"])
        sq = chess.parse_square(w["square"])
        line = w.get("engine_line")
        plan = f" The engine brings it to life: {line}." if line else ""
        frames = line_frames(board, line, sq) if line else None
        if w.get("you") == "named":
            out.append(_beat("worst_named", m["ply"], 0.5, f"Question 4, least active piece · move {m['move_number']}",
                             "You named your worst piece", f"Your {w['piece']} on {w['square']} could reach only "
                             f"{w['activity'] * 100:.0f}% of its squares, and you said so.{plan}", c.quote(m["ply"]),
                             board, fill={sq: BLUE}, frames=frames))
        elif not w.get("you"):
            out.append(_beat("worst", m["ply"], 0.35 + (0.25 - w["activity"]),
                             f"Question 4, least active piece · move {m['move_number']}", f"Your {w['piece']} was stuck",
                             f"On {w['square']} it reached {w['activity'] * 100:.0f}% of its squares, and it never came "
                             f"up in your thinking.{plan}", None, board, fill={sq: PURPLE}, frames=frames))
    return out


def line_frames(board: chess.Board, line_san: str, square: int, color: str = PURPLE, first: str = "Stuck here",
                last: str = ": it comes to life") -> list[dict]:
    """The engine's line, move by move, starting with `square` marked."""
    frames = [{"fen": board.fen(), "arrow": None, "last": None, "fill": {square: color}, "cap": first}]
    b = board.copy(stack=False)
    for tok in re.sub(r"\d+\.(\.\.)?", " ", line_san).split():
        try:
            mv = b.parse_san(tok)
        except ValueError:
            break
        label = _label(2 * b.fullmove_number - (1 if b.turn == chess.WHITE else 0), b.san(mv))
        b.push(mv)
        frames.append({"fen": b.fen(), "arrow": None, "last": (mv.from_square, mv.to_square), "fill": {}, "cap": label})
    frames[-1]["cap"] += last
    return frames


PIECE_NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook",
              chess.QUEEN: "queen", chess.KING: "king"}


def _weak_line(board: chess.Board, w: dict, square: int, color: str) -> tuple[str, list[dict] | None]:
    """Why a weak square matters: the engine's line that uses it, shown move by move, and what the
    piece that lands there hits from it."""
    line = w.get("engine_line")
    if not line:
        return "", None
    start = board.copy(stack=False)
    if w.get("if_passed"):
        start.push(chess.Move.null())
    frames = line_frames(start, line, square, color, f"{w['square']}", "")
    end = chess.Board(frames[-1]["fen"])
    piece = end.piece_at(square)
    hits = ""
    if piece and piece.piece_type != chess.PAWN:
        targets = sorted((s for s in end.attacks(square) if end.piece_at(s) and end.piece_at(s).color != piece.color
                          and end.piece_at(s).piece_type != chess.PAWN),
                         key=lambda s: -VALUE_OF[end.piece_at(s).piece_type])
        if targets:
            hits = (f" From {w['square']} the {PIECE_NAME[piece.piece_type]} hits "
                    + _and([f"the {PIECE_NAME[end.piece_at(s).piece_type]} on {chess.square_name(s)}" for s in targets[:3]]) + ".")
        else:
            hits = f" The {PIECE_NAME[piece.piece_type]} settles on {w['square']} and can't be driven away by a pawn."
    elif w["kind"] != "hole":
        hits = f" The pawn on {w['square']} falls."
    frames[-1]["cap"] += f": on {w['square']}"
    lead = "If you passed, the engine's line is" if w.get("if_passed") else "The engine's line:"
    return f" {lead} {line}.{hits}", frames


VALUE_OF = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 10}


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


MENTAL = {"tide", "gave_up", "instinct", "inner"}  # from psych.py: their own cards, not counted with "your thoughts"


def insights(c: Ctx) -> list[dict]:
    out = []
    for i in c.report.get("insights") or []:
        ply = i.get("ply") or 0
        m = c.by_ply.get(ply)
        frames = move_frames(c, mentioned_moves(c, i["body"]), predicted=i["kind"] == "read_plan") if c.played else []
        board = chess.Board(m["fen"]) if m and i["kind"] not in ("read_plan",) and not frames else None
        mental = i["kind"] in MENTAL
        move_no = m["move_number"] if m else (ply + 1) // 2 if ply else None
        eyebrow = ("The mental game" if mental else "Your thoughts") + (f" · move {move_no}" if move_no else "")
        if mental and not board and not frames and c.board_before(ply + 1) is not None:
            board = c.board_before(ply + 1)  # the position right after the moment
        out.append(_beat(i["kind"] if mental else "insight", ply, i.get("weight", 0.5) * (0.9 if mental else 0.75),
                         eyebrow, i["head"], _short(i["body"], 260), None, board, frames=frames))
    return out


def strike(c: Ctx) -> list[dict]:
    s = c.report.get("strike")
    if not s:
        return []
    if s.get("side"):
        board = c.board_before(s["ply"])
        arrows = []
        if board is not None:
            mv = c.game and list(c.game.mainline_moves())[s["ply"] - 1]
            arrows = [(mv.from_square, mv.to_square, ORANGE)] if mv else []
        good = s["side"] == "you" and s["outcome"] == "worked"
        text = s["text"]
        if s["side"] == "them" and c.played:  # did you draw their strike before they played it?
            san = c.boards[s["ply"] - 1].san(c.played[s["ply"] - 1]).rstrip("+#")
            mv = c.played[s["ply"] - 1]
            squares = f"{chess.square_name(mv.from_square)}-{chess.square_name(mv.to_square)}"
            ideas = {i.rstrip("+#") for m in c.moves if s["ply"] - 6 <= m["ply"] < s["ply"]
                     for i in m.get("drawn_opponent_ideas") or []}
            if san in ideas or squares in ideas:
                text += " You saw it coming: you'd drawn it."
        if s.get("break"):
            text += " Earlier, " + s["break"]["text"][0].lower() + s["break"]["text"][1:]
        beat = _beat("strike", s["ply"], 0.8 if good else 0.6, f"First strike · move {s['move_number']}",
                     "You struck first" if s["side"] == "you" else "They struck first", text,
                     c.quote(s["ply"]) if s["side"] == "you" else None, board, arrows)
        beat["note"] = STRIKE_EXPLAIN
        return [beat]
    if s.get("decided_by"):
        beat = _beat("decided", s["ply"], 0.4, f"First strike · move {(s['ply'] + 1) // 2}", "No strike needed",
                     s["text"].removeprefix("None: "), None, c.board_before(s["ply"] + 1))
        beat["note"] = STRIKE_EXPLAIN
        return [beat]
    return []


def clock(c: Ctx) -> list[dict]:
    tt_moves = [m for m in c.moves if c.in_tt(m)]
    if not tt_moves:
        return []
    lost = sum(m["wp_loss"] for m in tt_moves)
    if lost < 0.1:
        return []
    first = tt_moves[0]
    return [_beat("clock", first["ply"], lost * 1.5, f"The clock · move {first['move_number']}", "The clock took over",
                  f"From move {first['move_number']} you had under {c.tt:.0f}s. {pct(lost)} of win chance went in "
                  f"those {len(tt_moves)} moves.", c.quote(first["ply"]), stat=f"{first['clock_before']:.0f}s")]


def _few(x: float) -> str:
    """2.5 -> '2 or 3', 2 -> '2'."""
    return f"{int(x)} or {int(x) + 1}" if x != int(x) else f"{int(x)}"


def reply_seen(c: Ctx, m: dict) -> str | None:
    """Their reply to move m, when you'd drawn or said it before playing m (seen, not answered)."""
    if not c.played or m["ply"] >= len(c.played):
        return None
    reply = c.boards[m["ply"]].san(c.played[m["ply"]])
    ideas = [x.split(" (")[0].rstrip("+#") for x in m.get("drawn_opponent_ideas") or []]
    return reply if reply.rstrip("+#") in ideas else None


def masters_way(c: Ctx) -> list[dict]:
    """The deciding moment next to how masters handle the same kind of moment: whether a good move
    you'd found got dropped, and how wide the search was (master_baseline.json, via masters.py)."""
    drop_m, cands_m, many_m = masters.rate("trust"), masters.rate("search_cands"), masters.rate("search_many")
    costly = [m for m in c.moves if m["mistake"] and not c.in_tt(m)]
    if not costly or drop_m is None:
        return []
    m = max(costly, key=lambda m: m["wp_loss"])
    drawn = list(dict.fromkeys(d["move_san"] for d in m.get("drawn_candidates", []) if d.get("move_san")))
    best, label = m.get("best_move_san"), _label(m["ply"], m["move_san"])
    dropped = bool(best) and best in drawn and best != m["move_san"]
    wide = len(drawn) >= 8
    if not dropped and not wide:
        return []
    k, n = ((c.history or {}).get("yours") or {}).get("trust", (0, 0))
    parts = []
    if dropped:
        title = "Masters keep the move they find"
        parts.append(f"At {label}, {best} was among the moves you drew, and you played {m['move_san']}. Masters drop a "
                     f"good move they've drawn for a worse one about {drop_m:.0%} of the time"
                     + (f"; across your games you do it {k / n:.0%} of the time ({k} of {n})." if n >= 10 else "."))
    else:
        title = "Masters look at fewer moves"
    if wide:
        parts.append(f"You drew {len(drawn)} moves here. Masters usually weigh {_few(cands_m)} at a time and go to 8 or more on "
                     f"about {many_m:.0%} of their moves: they narrow it down first, then calculate the two that matter.")
    board = chess.Board(m["fen"])
    arrows = [_arrow(board, m["move_san"], DARK_BLUE), _arrow(board, best, GREEN) if best else None]
    return [_beat("masters", m["ply"], m["wp_loss"] * 2.6 + 0.25, f"How masters do it · move {m['move_number']}", title,
                  " ".join(parts), None, board, arrows, stat=went(m["wp_before"], m["wp_after"]))]


SOURCES = (turning_point, rushed, moods, comfortable, trust, threats, weaknesses, worst_piece, insights, strike, clock,
           masters_way)


def decisive(c: Ctx) -> bool:
    """Did one move decide the game? Costly enough on its own and most of everything you lost."""
    costly = [m["wp_loss"] for m in c.moves if m["mistake"]]
    return bool(costly) and max(costly) >= DECISIVE_LOSS and max(costly) >= DECISIVE_SHARE * sum(costly)


def pick(beats: list[dict], n: int = MAX_BEATS) -> list[dict]:
    """The best beats: one per move and one per kind (insights count by their own heads), each
    carrying at least RELATIVE of the top moment's weight."""
    chosen, plies, kinds = [], set(), set()
    ranked = sorted(beats, key=lambda b: -b["score"])
    floor = max(MIN_SCORE, RELATIVE * ranked[0]["score"]) if ranked else MIN_SCORE
    for b in ranked:
        if b["score"] < floor:
            break
        key = b["kind"] if b["kind"] != "insight" else "insight:" + b["title"]
        if b["kind"] == "masters":
            continue  # (added below, after its moment)
        if b["ply"] in plies or key in kinds:
            continue
        if b["kind"] == "insight" and sum(x["kind"] == "insight" for x in chosen) >= 2:
            continue
        chosen.append(b)
        plies.add(b["ply"])
        kinds.add(key)
        if len(chosen) == n:
            break
    # The masters card comes on top of the n, right after the moment it's about, when that made the cut.
    mb = max((b for b in beats if b["kind"] == "masters"), key=lambda b: b["score"], default=None)
    if mb and mb["ply"] in plies:
        chosen.append(mb)
    return sorted(chosen, key=lambda b: (b["ply"], b["kind"] == "masters"))


# ---------------------------------------------------------------- focus for next game

FOCUS = {
    "q3": ("Ask: what does my opponent want?", "Question 1, their idea",
           "Before every move, draw one arrow for the move they most want to play."),
    "candidates": ("Find a second candidate", "Question 3, candidates",
                   "Don't play until you've drawn two different moves for yourself."),
    "trust": ("Trust your candidates", "Instinct vs calculation",
              "When your first idea looks good, check it once more before switching to something new."),
    "q1": ("Ask: where are the weaknesses?", "Question 2, weaknesses",
           "Before a pawn move, circle the squares it stops guarding, on both sides."),
    "q2": ("Ask: which piece is doing least?", "Question 4, least active piece",
           "Make one of your candidates a move that brings your least active piece into play: that's how strong "
           "players answer this one."),
    "play_on": ("Play it out", "Your mindset",
                "When it feels lost, find the most stubborn move and keep thinking the same way: let the board "
                "decide it's over, not the mood."),
    "inner": ("Notice, don't judge", "Your mindset",
              "When a move goes wrong, say what happened in one plain sentence ('this drops the knight') and go "
              "back to the board. No verdicts on yourself."),
    "check": ("Look once more before you let go", "Blunder check",
              "Before you release the piece, ask one question: what does this move allow?"),
    "ahead": ("Stay alert when you're winning", "When you're ahead",
              "When the game is going your way, keep one question on every move: what are their checks, captures "
              "and threats? A good position is where their tactics hide."),
    "sharp": ("Slow down when it's sharp", "Time usage",
              "After a capture, a check or a surprise, ask before you move: is there only one move here? "
              "If there is, that's where your time goes."),
    "time": ("Bank time early", "Time usage",
             "Move faster in quiet positions before move 20, and spend the time where the game is sharp."),
    "input": ("Show your thinking", "Getting started",
              "Draw an arrow for each move you consider, theirs and yours, and say one sentence a move: a line, "
              "an opinion or how you feel."),
}
GUIDE = "https://thoughtcoach.org/play"  # how to draw and talk while you play
INPUT_MIN_MOVES, INPUT_SHARE = 10, 0.3  # under 30% of your moves with a thought: that comes first


def _thin_input(c: Ctx) -> str | None:
    """Why the review saw too little of your thinking to judge it, or None if it saw enough."""
    calm = [m for m in c.moves if not c.in_tt(m)]
    n = c.report["summary"].get("moves_with_thoughts", 0)
    if len(calm) < INPUT_MIN_MOVES or n >= INPUT_SHARE * len(calm):
        return None
    if not n:
        return "No arrows or words came through this game, so only your moves could be judged, not your thinking."
    return (f"Only {n} of your {len(calm)} moves had an arrow or words behind them, so most of your thinking "
            f"this game went unchecked.")


def aagaard_causes(c: Ctx) -> dict[str, list]:
    """Costly moves where the good move never came up, explained by one of Aagaard's questions:
    "q2" when the best move was the one that improves your worst piece and you never named it,
    "q1" when the engine's line went after a weakness you never marked. [(move, weakness/piece)]."""
    out: dict[str, list] = {"q1": [], "q2": []}
    for m in c.moves:
        if not m.get("mistake") or m.get("gap") not in ("missed_candidate", "no_thoughts") or c.in_tt(m):
            continue
        best = (m.get("best_move_san") or "").rstrip("+#")
        worst = (m.get("q2") or {}).get("worst")
        if worst and not worst.get("you") and best and (worst.get("engine_move") or "").rstrip("+#") == best:
            out["q2"].append((m, worst))
            continue
        weak = [w for w in (m.get("q1") or {}).get("weaknesses", []) if not w.get("you") and not w.get("created_by")]
        if weak:
            out["q1"].append((m, weak[0]))
    return out


def focus(c: Ctx, history: dict | None = None) -> dict:
    s = c.report["summary"]
    by = {k: v.get("wp_lost", 0) for k, v in s.get("by_gap", {}).items()}
    made = sum(m["wp_loss"] for m in c.moves
               if any(w.get("created_by") for w in (m.get("q1") or {}).get("weaknesses", [])))
    worst_missed = sum(1 for w in s.get("q2_list") or [] if not w.get("you"))
    score = {"q3": by.get("opponent_idea", 0), "candidates": by.get("missed_candidate", 0) + by.get("no_thoughts", 0),
             "trust": by.get("wrongly_rejected", 0), "time": by.get("time_trouble", 0), "q1": made,
             "q2": 0.03 * worst_missed,
             "check": 0.8 * sum(i.get("loss", 0) for i in c.report.get("insights") or [] if i["kind"] == "hindsight"),
             "play_on": sum(0.5 * (i["wp"] - 0.15) for i in c.report.get("insights") or [] if i["kind"] == "gave_up"),
             "inner": sum(0.5 * i.get("loss", 0) for i in c.report.get("insights") or [] if i["kind"] == "inner")}
    # The good move never came up: if one of Aagaard's questions explains why, it is that question's.
    causes = aagaard_causes(c)
    for q in ("q1", "q2"):
        lost = sum(m["wp_loss"] for m, _ in causes[q])
        score[q] += lost
        score["candidates"] -= lost
    # A costly move played fast where only one move held: the time is the cause (an unseen threat
    # or a missed weakness / worst piece stays with its question).
    explained = {m["ply"] for q in causes.values() for m, _ in q}  # plies a cause already explains
    # Comfortable, then caught: a costly move while you were winning (ahead.py).
    score["ahead"] = 0.0
    for m in c.caught:
        if m["ply"] in explained:
            continue
        score["ahead"] += m["wp_loss"]
        gap = c.by_ply.get(m["ply"], {}).get("gap")
        if gap == "wrongly_rejected":
            score["trust"] -= m["wp_loss"]
        elif gap in ("missed_candidate", "no_thoughts"):
            score["candidates"] -= m["wp_loss"]
        explained.add(m["ply"])
    score["sharp"] = 0.0
    for p in c.timing["rushed"]:
        if p["ply"] in explained or p["gap"] in ("opponent_idea", "time_trouble"):
            continue
        score["sharp"] += p["wp_loss"]
        if p["gap"] == "wrongly_rejected":
            score["trust"] -= p["wp_loss"]
        elif p["gap"] in ("missed_candidate", "no_thoughts"):
            score["candidates"] -= p["wp_loss"]
    reassigned = explained | {p["ply"] for p in c.timing["rushed"]}

    def costly(*gaps) -> list:
        return [m for m in c.moves if m["mistake"] and m.get("gap") in gaps and m["ply"] not in reassigned]

    made_moves = [m for m in c.moves if m["mistake"]
                  and any(w.get("created_by") for w in (m.get("q1") or {}).get("weaknesses", []))]
    why = {"q3": f"Threats left unstopped: {_tally(costly('opponent_idea'))}.",
           "candidates": f"Moves where the good move wasn't in your recorded thinking: {_tally(costly('missed_candidate', 'no_thoughts'))}.",
           "trust": f"Good moves you drew and then dropped: {_tally(costly('wrongly_rejected'))}.",
           "time": f"Moves in time trouble: {_tally([m for m in c.moves if m['mistake'] and m.get('gap') == 'time_trouble'])}.",
           "sharp": "",
           "ahead": "",
           "q1": f"Weaknesses your own moves created: {_tally(made_moves)}.",
           "q2": f"{worst_missed} passive piece{'s' * (worst_missed != 1)} didn't appear in your arrows or words.",
           "play_on": next((f"You called it lost while the engine still gave you {pct(i['wp'])}." for i in
                            c.report.get("insights") or [] if i["kind"] == "gave_up"), ""),
           "inner": "The judging voice cost you more than the position did.",
           "check": "You found the right idea, out loud, just after moving: " + next(
               (f"{i['count']} time{'s' * (i['count'] != 1)}." for i in
                c.report.get("insights") or [] if i["kind"] == "hindsight"), "")}
    thin = _thin_input(c)
    if thin:
        title, tag, habit = FOCUS["input"]
        return {"key": "input", "title": title, "tag": tag, "habit": habit, "why": thin, "across": None}
    hist = history or {}
    for k, share in hist.get("share", {}).items():
        score[k] = score.get(k, 0) + 0.15 * share
    # Where you're furthest from masters across your games (masters.py) leans a little toward that
    # habit; level with masters or better adds nothing.
    for k, ratio in hist.get("vs_masters", {}).items():
        if k in score:
            score[k] += 0.05 * min(2.0, max(0.0, ratio - 1))
    key = max(score, key=score.get)
    title, tag, habit = FOCUS[key]
    across = hist.get("note", {}).get(key)
    reason = why[key]
    if key == "ahead" and c.caught:
        m = c.caught[0]
        reason = (f"At {_label(m['ply'], m['move_san'])} you were at {pct(m['wp_before'])} and took {m['time']:.0f} s; "
                  f"{m['reply'] or 'their reply'} took it to {pct(m['wp_after'])}.")
        said = [r for r in c.tone.values() if r["mood"] == "upbeat" and r["quote"] and m["ply"] - 4 <= r["ply"] <= m["ply"]]
        if said:
            reason += f" Just before, you'd said “{said[-1]['quote']}”."
        across = (history or {}).get("ahead")
    if key == "q3":
        worst = max(costly("opponent_idea"), key=lambda m: m["wp_loss"], default=None)
        seen = reply_seen(c, worst) if worst else None
        if seen:  # you saw it; the move didn't answer it
            title, tag = "Answer the threat you saw", "Question 1, their idea"
            habit = ("When you've drawn their threat, play it on the board in your head after your chosen move: "
                     "does your move still hold? Check where your pieces go after it.")
            reason = (f"At {_label(worst['ply'], worst['move_san'])} you'd drawn their {seen}, and it came next: "
                      f"your win chance went {move_went(worst)}.")
            m3 = masters.rate("q3")
            if m3 is not None:
                across = (f"Masters leave about {m3:.0%} of real threats unstopped, through this same review. You'd "
                          f"seen this one: answering it is where this game went.")
    if key == "sharp" and c.timing["rushed"]:
        p = c.timing["rushed"][0]
        reason = (f"At {timing.label(p, c.white)} only {p['best']} held, and you took {p['time']:.0f} s. "
                  f"Your win chance went {went(p['wp_before'], p['wp_after'])}.")
        long = next((q for q in c.timing["lingered"] if q["ply"] < p["ply"]), None)
        if long:
            reason += f" You'd spent {long['time']:.0f} s on {timing.label(long, c.white)}, where several moves were fine."
    if key in ("q1", "q2") and causes[key]:
        m, w = max(causes[key], key=lambda x: x[0]["wp_loss"])
        move, cost = _label(m["ply"], m["move_san"]), f"took your win chance from {pct(m['wp_before'])} to {pct(m['wp_after'])}"
        if key == "q2":
            reason = (f"At {move} the best move was {w['engine_move']}, bringing your {w['piece']} on {w['square']} "
                      f"into the game. It doesn't appear in your arrows or words, and {m['move_san']} {cost}.")
        else:
            whose = "their" if w["side"] == "theirs" else "your"
            reason = (f"At {move} the engine's best line went after {whose} {w['kind']} on {w['square']}, which you "
                      f"none of your arrows or words pointed at. {m['move_san']} {cost}.")
            if w["side"] == "theirs":
                habit = ("Every few moves, circle the weakest point in their camp (a hole, a loose piece, a pawn "
                         "no pawn can defend) and aim a candidate at it.")
            else:
                habit = "Every few moves, circle the weakest point in your own camp, and ask how they would get at it."
    if key == "candidates":
        # Plenty of candidates, but the move that mattered was a forcing one you never looked at:
        # the habit isn't "more moves", it's checking the forcing ones first.
        worst = max((m for m in c.moves if m.get("gap") == "missed_candidate"), key=lambda m: m["wp_loss"], default=None)
        drawn = [d for d in (worst or {}).get("drawn_candidates", []) if d.get("legal")]
        best = (worst or {}).get("best_move_san") or ""
        if worst and len(drawn) >= 2 and ("+" in best or "x" in best or "#" in best):
            kind = "check" if "+" in best or "#" in best else "capture"
            title, tag = "Checks, captures and threats first", "Candidate moves"
            habit = "Before choosing, run through every check and capture, for both sides. It takes ten seconds."
            reason = (f"At {_label(worst['ply'], worst['move_san'])} you drew or said {len(drawn)} moves, but not "
                      f"{best}, a {kind}. {worst['move_san']} took your win chance from {pct(worst['wp_before'])} to "
                      f"{pct(worst['wp_after'])}.")
    return {"key": key, "title": title, "tag": tag, "habit": habit, "why": reason, "across": across}


def _tally(ms: list[dict]) -> str:
    """'3 times this game, the worst 22... Nf4 (75% → 56%)'."""
    if not ms:
        return "none this game"
    worst = max(ms, key=lambda m: m["wp_loss"])
    n = len(ms)
    head = "once this game" if n == 1 else f"{n} times this game, the worst"
    return f"{head} {_label(worst['ply'], worst['move_san'])} ({move_went(worst)})"



def history_from_profile(p: dict) -> dict:
    """Cross-game shares of the win chance lost by cause, and a sentence per focus, from profile.compute."""
    total = sum(v for _, v in p.get("gaps", [])) or 1
    g = dict(p.get("gaps", []))
    share = {"q3": g.get("opponent_idea", 0) / total, "candidates": (g.get("missed_candidate", 0) + g.get("no_thoughts", 0)) / total,
             "trust": g.get("wrongly_rejected", 0) / total, "time": g.get("time_trouble", 0) / total}
    n = len(p.get("games", []))
    q = p.get("questions", {})
    cause = {"q3": "threats left unstopped", "candidates": "moves where the good move wasn't in your recorded thinking",
             "trust": "good moves you drew and then dropped", "time": "moves in time trouble"}
    note = {k: f"Across your {n} games, {cause[k]} account for {pct(v)} of all the win chance you've lost."
            for k, v in share.items() if v and n >= 2}  # one game isn't a pattern
    if q.get("q3", [0, 0])[1] and n >= 2:
        real = q["q3"][1]
        stopped = q["q3"][0] + p.get("q3_handled", 0)  # named, or answered without a word
        note["q3"] = (f"Across your {n} games you stopped {stopped} of {real} real threats against you: "
                      f"{bayes.span(stopped, real)}.")
    if q.get("q1", [0, 0])[1] and n >= 2:
        note["q1"] = f"Across your {n} games you pointed at {q['q1'][0]} of {q['q1'][1]} weaknesses: {bayes.span(*q['q1'])}."
    if q.get("q2", [0, 0])[1] and n >= 2:
        note["q2"] = f"Across your {n} games you noticed {q['q2'][0]} of {q['q2'][1]} worst pieces: {bayes.span(*q['q2'])}."
    truths = []
    if n >= 3:
        from .profile import findings

        strong, weak = findings(p)
        sure = [dict(f, good=f in strong) for f in strong + weak if f.get("sure") is not None]
        truths = sorted(sure, key=lambda f: -f["sure"])[:3]
    al = p.get("ahead")
    return {"share": share, "note": note, "truths": truths, "games": n,
            "ahead": ahead.sentence(al) if al else None}


# ---------------------------------------------------------------- putting it together


def build(report: dict, analysis: dict, thinking: dict | None, game: chess.pgn.Game | None,
          history: dict | None = None) -> dict:
    c = Ctx(report, analysis, thinking, game)
    c.history = history
    c.ahead_note = (history or {}).get("ahead")
    one = decisive(c)
    every = [b for src in SOURCES for b in src(c)]
    beats = pick(every, DECISIVE_BEATS if one else MAX_BEATS)
    # Your voice gets a say: when it told something on its own and no such moment made the cut,
    # the strongest one is added.
    voiced = [b for b in every if b["kind"] in VOICE_KINDS and b["score"] >= MIN_SCORE]
    if voiced and not any(b["kind"] in VOICE_KINDS for b in beats):
        best = max(voiced, key=lambda b: b["score"])
        if best["ply"] not in {b["ply"] for b in beats}:
            beats = sorted(beats + [best], key=lambda b: b["ply"])
    # A study chapter to go deeper, for cards whose position a lichess course teaches.
    studies.attach(beats, lambda b: chess.Board(b["fen"]) if b.get("fen")
                   else c.boards[b["ply"] - 1] if 0 < b["ply"] <= len(c.boards) else None)
    worst = max((m for m in c.moves if m["mistake"]), key=lambda m: m["wp_loss"], default=None)
    decided_by = _label(worst["ply"], worst["move_san"]) if one and worst else None
    g = report["game"]
    me, them = (g["white"], g["black"]) if g["my_color"] == "white" else (g["black"], g["white"])
    arrows = said = 0
    for w in c.windows.values():
        for sh in w.get("shapes", []):
            if sh.get("source") == "voice":
                said += 1
            elif sh.get("source") == "arrow":
                arrows += 1
    res = g.get("result", "*")
    won = (res == "1-0") == (g["my_color"] == "white") and res in ("1-0", "0-1")
    outcome = "a draw" if res == "1/2-1/2" else "a win" if won else "a loss"
    curve = [(r["ply"], 1.0 if r["move_san"].endswith("#") else r["wp_after"])
             for r in analysis.get("moves", []) if r.get("wp_after") is not None]
    return {"id": g["id"], "me": me, "them": them, "color": g["my_color"], "tc": g.get("time_control"),
            "result": res, "outcome": outcome, "arrows": arrows, "said": said,
            "moves_with_thoughts": report["summary"].get("moves_with_thoughts", 0), "beats": beats,
            "curve": curve, "focus": focus(c, history), "orientation": c.me, "decided_by": decided_by,
            "type": classify(analysis, g), "voice": voice_card(c), "plies": _plies(c), "last_focus": (history or {}).get("last_focus"),
            "voice_off": report.get("voice") == "off",
            "truths": [{"head": f["head"], "body": f["body"], "sure": f["sure"], "n": f["n"], "good": f["good"]}
                       for f in (history or {}).get("truths", [])], "games": (history or {}).get("games")}


def _type_tag(t: dict | None) -> str:
    """'A swindle · You were down to 8% and won.' under the result."""
    return f"<p class='gtype'>{_e(t['label'])}<small>{_e(t['why'])}</small></p>" if t else ""


def _plies(c: Ctx) -> list[dict]:
    """Every position of the game for the clickable moves: [{"fen": placement, "label", "uci"}],
    index = plies played (0 = the start)."""
    if not c.boards:
        return []
    out = [{"fen": c.boards[0].board_fen(), "label": "the start", "uci": ""}]
    for i, mv in enumerate(c.played, 1):
        out.append({"fen": c.boards[i].board_fen(), "label": _label(i, c.boards[i - 1].san(mv)), "uci": mv.uci()})
    return out


SAN = r"(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?|O-O(?:-O)?)[+#]?"
MOVE_REF = re.compile(rf"\b(\d+)\.(\.\.)?\s?({SAN})|\b(move (\d+))\b")


def _link_moves(text: str, story: dict) -> str:
    """Moves written in a card ("27... Nxc3+", "move 11") as buttons that show that position."""
    plies = story.get("plies") or []
    if not plies:
        return text
    white = story.get("color") == "white"

    def swap(m: re.Match) -> str:
        if m.group(3):
            n, black = int(m.group(1)), bool(m.group(2))
            ply = 2 * n if black else 2 * n - 1
            if not (0 < ply < len(plies)) or plies[ply]["label"].split()[-1] != m.group(3):
                return m.group(0)
            return f"<button type='button' class='mv' data-ply='{ply}'>{m.group(0)}</button>"
        n = int(m.group(5))
        ply = (2 * n - 1 if white else 2 * n) - 1  # the position you faced at your move n
        if not (0 <= ply < len(plies)):
            return m.group(0)
        return f"<button type='button' class='mv' data-ply='{ply}'>{m.group(4)}</button>"

    return MOVE_REF.sub(swap, text)


def summary(story: dict) -> dict:
    """What the home page needs from a story (saved as <id>.story.json): no animation frames."""
    keep = ("kind", "ply", "eyebrow", "title", "body", "quote", "fen", "arrows", "fill", "stat", "note", "study")
    return {**{k: story[k] for k in ("id", "me", "them", "color", "tc", "result", "outcome", "arrows", "said",
                                     "moves_with_thoughts", "curve", "focus", "decided_by")},
            "voice": story.get("voice"),
            "type": story.get("type"),
            "orientation": "white" if story["orientation"] else "black",
            "beats": [{k: b.get(k) for k in keep} for b in story["beats"]]}


def _frames_svg(b: dict, orientation: chess.Color) -> str:
    """A board that plays through moves: the frames are stacked and shown one at a time."""
    svgs = []
    for f in b["frames"]:
        board = chess.Board(f["fen"])
        arrows = [chess.svg.Arrow(f["arrow"][0], f["arrow"][1], color=f["arrow"][2] + "dd")] if f["arrow"] else []
        last = chess.Move(*f["last"]) if f["last"] else None
        fill = {sq: col + "cc" for sq, col in (f.get("fill") or {}).items()}
        svg = chess.svg.board(board, orientation=orientation, arrows=arrows, lastmove=last, coordinates=False, size=420,
                              fill=fill)
        svgs.append(f"<div class='fr' data-cap='{_e(f['cap'])}'>{svg}</div>")
    return f"<div class='board anim'>{''.join(svgs)}</div><p class='framecap'></p>"


def _board_svg(b: dict, orientation: chess.Color) -> str:
    if not b.get("fen"):
        return ""
    board = chess.Board(b["fen"])
    arrows = [chess.svg.Arrow(f, t, color=col + "dd") for f, t, col in b["arrows"]]
    return chess.svg.board(board, orientation=orientation, arrows=arrows, coordinates=False, size=420,
                           fill={sq: col + "cc" for sq, col in b["fill"].items()})


KEY_ARROW = {BLUE: "you considered", DARK_BLUE: "you played", GREEN: "engine's best", RED: "their threat",
             ORANGE: "the strike"}
KEY_FILL = {ORANGE: "the weakness", BLUE: "you saw it", PURPLE: "stuck piece"}


def _key(b: dict) -> str:
    items = []
    for _, _, col in b["arrows"]:
        if (col, KEY_ARROW.get(col)) not in items and col in KEY_ARROW:
            items.append((col, KEY_ARROW[col]))
    for col in b["fill"].values():
        if col in KEY_FILL and (col, KEY_FILL[col]) not in items:
            items.append((col, KEY_FILL[col]))
    return "<p class='key'>" + "".join(f"<span><i style='background:{c}'></i>{_e(t)}</span>" for c, t in items) + "</p>" if items else ""


def _voice_slide(story: dict) -> str:
    v = story["voice"]
    c = v["counts"]
    W, H, PAD, TOP = 600, 170, 16, 18
    curve = story["curve"]
    svg = ""
    if len(curve) >= 2:
        p0, p1 = curve[0][0], curve[-1][0]
        x = lambda p: PAD + (min(max(p, p0), p1) - p0) / max(1, p1 - p0) * (W - 2 * PAD)  # noqa: E731
        y = lambda w: TOP + (1 - w) * (H - TOP - 8)  # noqa: E731
        pts = " ".join(f"{x(p):.1f},{y(w):.1f}" for p, w in curve)
        dots = "".join(f"<circle cx='{x(p):.1f}' cy='{y(w):.1f}' r='{7 if mood != 'neutral' else 4}' "
                       f"style='fill:{MOOD_COLOR[mood]}'><title>move {(p + 1) // 2}: {MOOD_WORD[mood]}</title></circle>"
                       for p, mood, w in v["marks"])
        svg = (f"<svg class='curve mood' viewBox='0 0 {W} {H}' aria-label='Your win chance, with what you sounded like'>"
               f"<line x1='{PAD}' x2='{W - PAD}' y1='{y(0.5):.1f}' y2='{y(0.5):.1f}'/>"
               f"<polyline points='{pts}'/>{dots}</svg>")
    key = "".join(f"<span><i style='background:{MOOD_COLOR[k]}'></i>{MOOD_WORD[k]} {c[k]}</span>"
                  for k in ("upbeat", "worried", "neutral") if c[k])
    q = v.get("quote")
    quote = (f"<blockquote>“{_e(q['text'])}”<cite>you, around {_e(q['move'])}</cite></blockquote>" if q else "")
    line = f"<p class='body'>{_e(v['line'])}</p>" if v.get("line") else ""
    return (f"<p class='eyebrow'>Your voice</p><h2>You talked through {v['talked']} of {v['of']} moves</h2>"
            f"<div class='visual wide'>{svg}</div><p class='key moodkey'>{key}</p>{quote}{line}")


def _curve_svg(curve, marks) -> str:
    if len(curve) < 2:
        return ""
    W, H, PAD, TOP = 600, 170, 16, 26  # room at the edges and on top for the numbers
    p0, p1 = curve[0][0], curve[-1][0]
    x = lambda p: PAD + (p - p0) / max(1, p1 - p0) * (W - 2 * PAD)  # noqa: E731
    y = lambda v: TOP + (1 - v) * (H - TOP - 8)  # noqa: E731
    pts = " ".join(f"{x(p):.1f},{y(v):.1f}" for p, v in curve)
    dots = "".join(f"<circle cx='{x(p):.1f}' cy='{y(v):.1f}' r='8'/><text x='{x(p):.1f}' y='{y(v) - 13:.1f}'>{i}</text>"
                   for i, (p, v) in enumerate(marks, 1))
    return (f"<svg class='curve' viewBox='0 0 {W} {H}' aria-label='Your win chance through the game'>"
            f"<line x1='{PAD}' x2='{W - PAD}' y1='{y(0.5):.1f}' y2='{y(0.5):.1f}'/>"
            f"<text class='ax' x='{W - PAD}' y='{y(0.5) - 5:.1f}'>50%</text><polyline points='{pts}'/>{dots}</svg>")


def _study(b: dict) -> str:
    """The lichess study chapter to go deeper, credited to its author."""
    st = b.get("study")
    if not st:
        return ""
    return (f"<p class='study'>Go deeper on {_e(st['about'])}: <a href='{_e(st['url'])}' target='_blank' rel='noopener'>"
            f"{_e(st['chapter'])}</a> <span>in {_e(st['study'])}, a lichess study by {_e(st['by'])}</span></p>")


VOICE_HINT = ("<p class='body' style='opacity:.75;font-size:.9em'>Voice was off this game, so this story comes from your "
              "arrows. Switch it on from the Thought Coach icon and the next one can also tell you what you were "
              "thinking.</p>")


def render(story: dict) -> str:
    slides = []
    o = story["orientation"]
    curve = {p: v for p, v in story["curve"]}
    marks = []
    for b in story["beats"]:
        v = curve.get(b["ply"]) or curve.get(b["ply"] + 1) or curve.get(b["ply"] - 1)
        if v is not None:
            marks.append((b["ply"], v))
    verdict = {"a win": "You won", "a loss": "You lost", "a draw": "A draw"}[story["outcome"]]
    n = len(story["beats"])
    if story.get("decided_by"):
        lead = f"It came down to one move: {story['decided_by']}."
    elif n:
        lead = f"The numbers are the {n} moments ahead."
    else:
        lead = "Nothing stood out enough for its own card."
    slides.append(("intro", f"""<p class='eyebrow'>{_e(story['tc'] or '')} · you had {_e(story['color'])} · {_e(story['result'])}</p>
<h1>{_e(story['me'])}<span>vs</span>{_e(story['them'])}</h1>
<p class='verdict'>{verdict}</p>{_type_tag(story.get('type'))}<div class='visual wide'>{_curve_svg(story['curve'], marks)}</div>
<p class='lead'>Your win chance, move by move. {lead}</p>
<div class='stats'><div><b>{story['arrows']}</b>arrows drawn</div><div><b>{story['said']}</b>things said aloud</div>
<div><b>{story['moves_with_thoughts']}</b>moves with thoughts</div></div>"""))
    if story.get("voice"):
        slides.append(("voice", _voice_slide(story)))
    for i, b in enumerate(story["beats"], 1):
        meta = f"<span hidden class='meta' data-ply='{b['ply']}'></span>"
        board = _frames_svg(b, o) if b.get("frames") else _board_svg(b, o)
        stat = f"<div class='stat'>{_e(b['stat'])}</div>" if b.get("stat") else ""
        inside = board if b.get("frames") else f"<div class='board'>{board}</div>{_key(b)}" if board else ""
        visual = f"<div class='visual'>{inside}</div>" if inside else ""
        quote = f"<blockquote>“{_e(b['quote'])}”<cite>you, during the game</cite></blockquote>" if b.get("quote") else ""
        slides.append((b["kind"], meta + f"""<p class='eyebrow'><span class='n'>{i}</span>{_e(b['eyebrow'])}</p>
<h2>{_e(b['title'])}</h2>{stat}{visual}
<p class='body'>{_link_moves(_e(b['body']), story)}</p>{quote}{f"<p class='note'>{_e(b['note'])}</p>" if b.get('note') else ''}{_study(b)}"""))
    f = story["focus"]
    if story.get("truths"):
        items = "".join(
            f"<div class='truth {'good' if t['good'] else 'weak'}'><p class='th'>{_e(t['head'])}</p><p class='tb'>{_e(t['body'])}</p>"
            f"<p class='ts'><span>{_e(bayes.verdict(t['sure']).capitalize())} · "
            f"{t['n']} moves</span></p></div>"
            for t in story["truths"])
        slides.append(("truths", f"""<p class='eyebrow'>Across your {story.get('games') or ''} games</p><h2>What your games say</h2>
<p class='lead'>One game can be luck. These held up across all of them.</p><div class='truths'>{items}</div>"""))
    lf = story.get("last_focus")
    last = (f"<p class='lastfocus'><span>Last game's focus: <b>{_e(lf['title'])}</b></span>{_link_moves(_e(lf['line']), story)}"
            + (f"<small>{_e(lf['check'])}</small>" if lf.get("check") else "") + "</p>"
            if lf and lf.get("line") else "")
    slides.append(("focus", f"""{last}<p class='eyebrow'>Your focus for the next game · {_e(f['tag'])}</p><h2>{_e(f['title'])}</h2>
<p class='habit'>{_e(f['habit'])}</p><p class='body'>{_link_moves(_e(f['why']), story)}{(' ' + _e(f['across'])) if f.get('across') else ''}</p>
{VOICE_HINT if story.get('voice_off') else ''}<div class='end'>{f"<a href='{GUIDE}'>How to show your thinking</a>" if f['key'] == 'input' else ''}<a href='{_e(game_url(story['id']))}'>See the full review</a><a href='https://lichess.org/{_e(story['id'])}{"/black" if story.get("color") == "black" else ""}' target='_blank' rel='noopener'>Analyze on lichess</a><a href='{_e(home_url())}'>All your games</a>
<button type='button' class='replay'>Watch again</button></div>"""))
    body = "".join(f"<section class='slide' data-kind='{k}' style='--accent:{ACCENT.get(k, '#495057')}'><div class='inner'>{h}</div></section>"
                   for k, h in slides)
    bars = "".join("<i><b></b></i>" for _ in slides)
    # Built outside the f-string: a backslash inside its braces needs Python 3.12 (the worker runs 3.11).
    plies_json = json.dumps({"plies": story.get("plies") or [], "white": story.get("color") != "black"}).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Your game · {_e(story['id'])}</title>
<style>{CSS}</style></head><body><main class="story" aria-live="polite">
<div class="bars">{bars}</div><button class="close" type="button" onclick="location.href='{_e(game_url(story['id']))}'" aria-label="Close">×</button>
{body}<button class="nav prev" type="button" aria-label="Previous card"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button>
<button class="nav next" type="button" aria-label="Next card"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button>
<button class="flag" type="button" aria-haspopup="dialog">Feedback on this card</button>
<div class="fb" role="dialog" aria-label="Feedback on this card" hidden><p class="fbh">How is this card?</p>
<label><input type="radio" name="why" value="accurate" checked> Accurate</label>
<label><input type="radio" name="why" value="helpful"> Helpful: I learned something</label>
<label><input type="radio" name="why" value="unrecorded"> I did consider this, I just didn't draw or say it</label>
<label><input type="radio" name="why" value="misread"> It misreads what I was thinking</label>
<label><input type="radio" name="why" value="wrong"> It's wrong about the position</label>
<label><input type="radio" name="why" value="confusing"> It's confusing</label>
<label><input type="radio" name="why" value="other"> Something else</label>
<textarea maxlength="1000" placeholder="Tell us more (optional)"></textarea>
<div class="fbr"><button type="button" class="fbsend">Send</button><button type="button" class="fbcancel">Cancel</button></div>
<p class="fbdone" hidden>Thanks: every answer is read, and it helps check the coaching.</p></div>
<p class="hint"><span class="count"></span><span class="keys"> · use ← → to move between cards</span></p></main>
<script type="application/json" id="plies">{plies_json}</script>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">{PIECE_DEFS}</svg>
<script>{JS}{MOVES_JS}</script></body></html>"""


PIECE_DEFS = re.search(r"<defs>.*?</defs>", chess.svg.board(chess.Board(), coordinates=False), re.S).group(0)

MOVES_JS = """
(() => {
  // A move named in a card: show that position on the card's board (and go back).
  const data = JSON.parse(document.getElementById('plies').textContent);
  const P = data.plies; if (!P.length) return;
  const NAME = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' };
  const sqXY = (sq) => { const f = sq.charCodeAt(0) - 97, r = +sq[1] - 1;
    return data.white ? [f * 45, (7 - r) * 45] : [(7 - f) * 45, r * 45]; };
  function svg(ply) {
    const rows = P[ply].fen.split('/'), last = P[ply].uci, hi = last ? [last.slice(0, 2), last.slice(2, 4)] : [];
    let out = '';
    for (let r = 7; r >= 0; r--) for (let f = 0; f < 8; f++) {
      const sq = 'abcdefgh'[f] + (r + 1), [x, y] = sqXY(sq), light = (f + r) % 2 === 1;
      const on = hi.includes(sq);
      out += `<rect x="${x}" y="${y}" width="45" height="45" fill="${on ? (light ? '#cdd16a' : '#aaa23b') : (light ? '#ffce9e' : '#d18b47')}"/>`;
    }
    rows.forEach((row, i) => { let f = 0; for (const ch of row) {
      if (/\\d/.test(ch)) { f += +ch; continue; }
      const sq = 'abcdefgh'[f] + (8 - i), [x, y] = sqXY(sq);
      const id = (ch === ch.toUpperCase() ? 'white-' : 'black-') + NAME[ch.toLowerCase()];
      out += `<use href="#${id}" xlink:href="#${id}" transform="translate(${x}, ${y})"/>`; f++; } });
    return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 360 360">${out}</svg>`;
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('.mv'), back = e.target.closest('.mvback');
    const slide = (b || back) && (b || back).closest('.slide');
    if (!slide) return;
    let board = slide.querySelector('.board');
    if (!board) {  // a card without a board: give it one
      const v = document.createElement('div'); v.className = 'visual'; v.dataset.made = '1'; v.innerHTML = '<div class="board"></div>';
      slide.querySelector('.inner').prepend(v); board = v.querySelector('.board');
    }
    if (board.dataset.orig === undefined) board.dataset.orig = board.innerHTML;
    let cap = slide.querySelector('.mvcap');
    if (back) { const made = board.closest('[data-made]'); if (made) made.remove(); else board.innerHTML = board.dataset.orig;
      if (cap) cap.remove();
      slide.querySelectorAll('.mv.on').forEach(x => x.classList.remove('on')); return; }
    const ply = +b.dataset.ply;
    board.innerHTML = svg(ply);
    slide.querySelectorAll('.mv.on').forEach(x => x.classList.remove('on')); b.classList.add('on');
    if (!cap) { cap = document.createElement('p'); cap.className = 'mvcap'; board.after(cap); }
    cap.innerHTML = `${ply ? 'After ' + P[ply].label : 'The start'} &middot; <button type="button" class="mvback">back to this card</button>`;
  });
})();
"""

CSS = """
* { box-sizing:border-box; } html, body { margin:0; height:100%; background:#0b0b0c; color:#fff; overflow:hidden;
  font:17px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
.story { position:relative; height:100%; max-width:560px; margin:0 auto; }
.slide { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; padding:56px 22px 40px;
  background:radial-gradient(120% 80% at 50% 0%, color-mix(in srgb, var(--accent) 70%, #000) 0%, #0b0b0c 70%);
  opacity:0; visibility:hidden; transition:opacity .45s ease, visibility .45s; }
.slide.on { opacity:1; visibility:visible; }
.inner { width:100%; display:flex; flex-direction:column; gap:12px; }
.slide.on .inner > * { animation:rise .6s ease both; } .slide.on .inner > *:nth-child(2) { animation-delay:.12s; }
.slide.on .inner > *:nth-child(3) { animation-delay:.24s; } .slide.on .inner > *:nth-child(4) { animation-delay:.36s; }
.slide.on .inner > *:nth-child(5) { animation-delay:.48s; } .slide.on .inner > *:nth-child(6) { animation-delay:.6s; }
@keyframes rise { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce) { .slide { transition:none; } .slide.on .inner > * { animation:none; } }
.eyebrow { margin:0; font-size:12.5px; letter-spacing:.08em; text-transform:uppercase; color:#ffffffb3; display:flex; align-items:center; gap:8px; }
.eyebrow .n { width:22px; height:22px; border-radius:50%; background:#fff; color:#000; display:grid; place-items:center; font-weight:800; letter-spacing:0; }
h1 { font-size:clamp(34px, 9vw, 52px); line-height:1.05; margin:4px 0; display:flex; flex-direction:column; }
h1 span { font-size:.45em; color:#ffffff99; font-weight:500; margin:4px 0; }
h2 { font-size:clamp(28px, 7.5vw, 42px); line-height:1.08; margin:0; }
.stat { font-size:clamp(44px, 14vw, 72px); font-weight:800; line-height:1; color:var(--accent); filter:brightness(1.5); }
.board { width:min(100%, 42vh); align-self:center; border-radius:6px; overflow:hidden; box-shadow:0 12px 40px #0008; }
.board svg { display:block; width:100%; height:auto; }
.board.anim { display:grid; } .board.anim .fr { grid-area:1 / 1; opacity:0; transition:opacity .35s ease; } .board.anim .fr.on { opacity:1; }
.framecap { margin:-2px 0 0; text-align:center; font-weight:700; font-size:16px; min-height:1.4em; }
.story { user-select:none; -webkit-user-select:none; -webkit-tap-highlight-color:transparent; }
.body { margin:0; color:#ffffffe6; }
.key { margin:-4px 0 0; display:flex; flex-wrap:wrap; justify-content:center; gap:4px 14px; font-size:12.5px; color:#ffffffb3; }
.key span { display:inline-flex; align-items:center; gap:6px; } .key i { width:12px; height:12px; border-radius:3px; display:inline-block; } .lead { margin:8px 0 0; font-size:19px; color:#ffffffe6; }
blockquote { margin:0; padding:10px 14px; border-left:3px solid var(--accent); background:#ffffff10; border-radius:0 8px 8px 0;
  font-size:19px; font-style:italic; } blockquote cite { display:block; margin-top:4px; font-size:12px; font-style:normal; color:#ffffff99; }
.stats { display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; margin-top:6px; }
.stats div { background:#ffffff12; border-radius:12px; padding:12px 10px; font-size:13px; color:#ffffffb3; }
.stats b { display:block; font-size:30px; color:#fff; line-height:1.1; }
.habit { margin:0; font-size:22px; font-weight:600; padding:14px 16px; border-radius:12px; background:#ffffff14; }
.gtype { display:inline-block; margin:8px 0 0; padding:4px 12px; border-radius:20px; font-size:14px; font-weight:700;
  background:color-mix(in srgb, var(--accent) 22%, transparent); color:#fff; }
.gtype small { font-weight:400; opacity:.8; margin-left:6px; font-size:13px; }
.verdict { margin:0; font-size:clamp(26px, 7vw, 38px); font-weight:800; color:var(--accent); filter:brightness(1.6); }
.curve { width:100%; height:auto; display:block; } .curve polyline { fill:none; stroke:#fff; stroke-width:3; }
.curve .ax { fill:#ffffff80; font-size:12px; font-weight:400; text-anchor:end; }
.curve line { stroke:#ffffff40; stroke-dasharray:4 4; }
.visual { display:flex; flex-direction:column; gap:10px; align-items:center; }
/* Desktop: the board (or chart) on the left, the words on the right. */
@media (min-width: 900px) and (min-height: 520px) {
  .story { max-width:1180px; }
  .slide { padding:64px 104px 44px; }
  .hint { bottom:12px; }
  .inner { max-width:1080px; margin:0 auto; }
  .inner:has(> .visual:not(.wide)) { display:grid; grid-template-columns:minmax(0, 1.05fr) minmax(0, 1fr); column-gap:56px;
    align-items:center; align-content:center; }
  .inner:has(> .visual:not(.wide)) > * { grid-column:2; }
  .inner:has(> .visual:not(.wide)) > .visual { grid-column:1; grid-row:1 / span 8; align-self:center; }
  .visual .board { width:min(100%, 72vh); }
  h1 { font-size:58px; } h2 { font-size:46px; } .body, .lead { font-size:20px; } blockquote { font-size:21px; }
  .stats b { font-size:36px; } .habit { font-size:26px; }
  .inner:not(:has(> .visual:not(.wide))) { max-width:820px; }
  .nav { top:50%; bottom:auto; transform:translateY(-50%); width:56px; height:56px; }
  .nav:hover { transform:translateY(-50%) scale(1.07); }
  .nav.prev { left:24px; } .nav.next { right:24px; }
}
.curve.mood circle { stroke:#0b0b0c; stroke-width:1.5; }
.moodkey { justify-content:flex-start; }
.mv { font:inherit; color:inherit; background:#ffffff1a; border:0; border-bottom:1px dashed #ffffffaa; border-radius:4px;
  padding:0 3px; cursor:pointer; } .mv:hover, .mv.on { background:#ffffff38; } .mv:focus-visible { outline:2px solid #fff; }
.mvcap { margin:6px 0 0; text-align:center; font-size:13px; color:#ffffffcc; }
.mvback { font:inherit; font-size:13px; color:#fff; background:none; border:0; text-decoration:underline; cursor:pointer; padding:0; }
.study { margin:8px 0 0; font-size:13px; line-height:1.45; color:#ffffffcc; }
.study a { color:#fff; font-weight:600; text-decoration:underline; text-underline-offset:2px; }
.study span { color:#ffffff99; }
.note { margin:0; font-size:13px; line-height:1.45; color:#ffffff99; border-top:1px solid #ffffff22; padding-top:8px; }
.lastfocus { margin:0 0 10px; padding:10px 14px; border-radius:10px; background:#ffffff12; font-size:15px; color:#ffffffd9; }
.flag { position:absolute; top:26px; right:12px; z-index:6; background:none; border:0; color:#ffffff73; font:inherit;
  font-size:12px; cursor:pointer; padding:4px 6px; border-radius:6px; }
.flag:hover, .flag:focus-visible { color:#fff; background:#ffffff1a; outline:none; }
.fb { position:absolute; top:58px; right:12px; z-index:7; width:min(320px, calc(100% - 24px)); background:#1b1b1a;
  border:1px solid #ffffff2e; border-radius:14px; padding:14px; box-shadow:0 12px 40px #000a; font-size:14px; }
.fb[hidden] { display:none; } .fb .fbh { margin:0 0 8px; font-weight:700; }
.fb label { display:flex; gap:8px; align-items:center; margin:4px 0; color:#ffffffd9; cursor:pointer; }
.fb textarea { width:100%; min-height:70px; margin:8px 0; border-radius:8px; border:1px solid #ffffff2e; background:#0b0b0c;
  color:#fff; font:inherit; font-size:13.5px; padding:8px; resize:vertical; }
.fbr { display:flex; gap:8px; } .fbr button { font:inherit; font-size:13.5px; border-radius:999px; padding:7px 14px; cursor:pointer; border:0; }
.fbsend { background:#fff; color:#000; font-weight:700; } .fbcancel { background:#ffffff1f; color:#fff; }
.fbdone { margin:6px 0 0; color:#8ce99a; }
.lastfocus small { display:block; margin-top:4px; font-size:13px; color:#ffffffb3; }
.truths { display:grid; gap:10px; }
.truth { padding:12px 14px; border-radius:12px; background:#ffffff12; border-left:3px solid #ffffff55; }
.truth.good { border-left-color:#51cf66; } .truth.weak { border-left-color:#ff922b; }
.truth .th { margin:0; font-weight:700; font-size:17px; } .truth .tb { margin:2px 0 6px; font-size:15px; color:#ffffffd9; }
.truth .ts { margin:0; display:flex; align-items:center; gap:10px; font-size:13px; color:#ffffffb3; }
.odots { width:118px; height:10px; flex:none; } .odots .on { fill:#fff; } .odots .off { fill:#ffffff33; }
.lastfocus span { display:block; font-size:12.5px; color:#ffffffa6; margin-bottom:2px; } .lastfocus b { color:#fff; }
.curve circle { fill:#f08c00; stroke:#0b0b0c; stroke-width:2; } .curve text { fill:#fff; font-size:14px; font-weight:700; text-anchor:middle; }
.end { display:flex; flex-wrap:wrap; gap:10px; margin-top:8px; position:relative; z-index:5; }
.end a, .end button { font:inherit; font-size:15px; color:#000; background:#fff; border:0; border-radius:999px; padding:10px 16px;
  text-decoration:none; cursor:pointer; } .end button { background:#ffffff26; color:#fff; }
.bars { position:absolute; top:12px; left:12px; right:48px; display:flex; gap:4px; z-index:4; }
.bars i { flex:1; height:3px; background:#ffffff40; border-radius:2px; overflow:hidden; }
.bars b { display:block; height:100%; width:0; background:#fff; transition:width .35s ease; }
.close { position:absolute; top:4px; right:8px; z-index:6; background:none; border:0; color:#fff; font-size:28px; cursor:pointer; }
.nav { position:absolute; bottom:22px; z-index:5; width:48px; height:48px; border-radius:50%; display:grid; place-items:center;
  color:#fff; background:#ffffff1a; border:1px solid #ffffff38; box-shadow:0 6px 24px #0006; cursor:pointer;
  -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px); transition:background .2s, transform .2s, opacity .25s; }
.nav svg { width:24px; height:24px; fill:none; stroke:currentColor; stroke-width:2.6; stroke-linecap:round; stroke-linejoin:round; }
.nav:hover { background:#ffffff33; transform:scale(1.07); } .nav:active { background:#ffffff4d; }
.nav:focus-visible { outline:2px solid #fff; outline-offset:3px; }
.nav.prev { left:18px; } .nav.next { right:18px; }
.nav[disabled] { opacity:0; pointer-events:none; }
.nav.next.invite { animation:invite 1.6s ease-in-out 1.2s 3; }
@keyframes invite { 0%, 100% { box-shadow:0 6px 24px #0006, 0 0 0 0 #ffffff55; } 50% { box-shadow:0 6px 24px #0006, 0 0 0 10px #ffffff00; } }
@media (prefers-reduced-motion: reduce) { .nav.next.invite { animation:none; } }
.slide { padding-bottom:92px; }
.hint { position:absolute; bottom:8px; width:100%; text-align:center; font-size:11.5px; color:#ffffff73; margin:0; z-index:4;
  pointer-events:none; }
@media (hover: none) { .hint .keys { display:none; } }
"""

JS = """
(() => {
  const slides = [...document.querySelectorAll('.slide')], bars = [...document.querySelectorAll('.bars b')];
  const prev = document.querySelector('.nav.prev'), next = document.querySelector('.nav.next');
  const count = document.querySelector('.hint .count');
  const FRAME_MS = 2200;  // a board that plays through moves shows each frame this long
  let i = 0, frameTimer = 0;
  function frames(slide) {
    clearInterval(frameTimer);
    const fr = [...slide.querySelectorAll('.fr')], cap = slide.querySelector('.framecap');
    if (!fr.length) return;
    let k = 0;
    const step = () => { fr.forEach((f, j) => f.classList.toggle('on', j === k)); cap.textContent = fr[k].dataset.cap; };
    step();
    frameTimer = setInterval(() => { k = (k + 1) % fr.length; step(); }, FRAME_MS);
  }
  function show(n) {
    i = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach((s, k) => s.classList.toggle('on', k === i));
    bars.forEach((b, k) => b.style.width = k <= i ? '100%' : '0');
    prev.disabled = i === 0;
    next.disabled = i === slides.length - 1;
    count.textContent = `${i + 1} / ${slides.length}`;
    frames(slides[i]);
  }
  prev.onclick = () => show(i - 1);
  next.onclick = () => { next.classList.remove('invite'); show(i + 1); };
  const replay = document.querySelector('.replay');
  if (replay) replay.onclick = () => show(0);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !fb.hidden) { closeFb(); return; }
    if (busy(e)) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); next.classList.remove('invite'); show(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); show(i - 1); }
    else if (e.key === 'Home') show(0); else if (e.key === 'End') show(slides.length - 1);
  });
  // Swipe on a touch screen: left for the next card, right to go back.
  let x0 = null, y0 = null;
  document.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  document.addEventListener('touchend', e => {
    if (x0 === null || busy(e)) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) show(i + (dx < 0 ? 1 : -1));
    x0 = null;
  });
  // "Doesn't make sense?": report the card on screen (kept privately, read to improve the stories).
  const flag = document.querySelector('.flag'), fb = document.querySelector('.fb');
  const m = location.pathname.match(/\\/u\\/([a-z0-9_-]+)\\/games\\/([A-Za-z0-9]{8})/) || location.pathname.match(/\\/games\\/([A-Za-z0-9]{8})/);
  const owner = m && m.length > 2 ? m[1] : '', gid = m ? m[m.length - 1] : '';
  const closeFb = () => { fb.hidden = true; flag.focus(); };
  flag.onclick = () => {
    fb.hidden = !fb.hidden;
    fb.querySelector('.fbdone').hidden = true; fb.querySelector('.fbr').hidden = false;
    if (!fb.hidden) fb.querySelector('input:checked').focus();
  };
  fb.querySelector('.fbcancel').onclick = closeFb;
  fb.querySelector('.fbsend').onclick = async () => {
    const s = slides[i], meta = s.querySelector('.meta');
    const body = { owner, game_id: gid, reason: fb.querySelector('input:checked').value,
      comment: fb.querySelector('textarea').value,
      card: { index: i, kind: s.dataset.kind, ply: meta ? +meta.dataset.ply : null,
              title: (s.querySelector('h1, h2') || {}).innerText || '', text: s.innerText.slice(0, 1500) } };
    try {
      await fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (err) { /* offline: nothing to do */ }
    fb.querySelector('.fbr').hidden = true; fb.querySelector('.fbdone').hidden = false;
    fb.querySelector('textarea').value = '';
    setTimeout(() => { fb.hidden = true; }, 1600);
  };
  const busy = (e) => !fb.hidden || (e.target && e.target.closest && e.target.closest('.fb'));
  next.classList.add('invite');
  show(0);
})();
"""


def write(path, story: dict) -> None:
    path.write_text(render(story), encoding="utf-8")


def to_json(story: dict) -> str:
    return json.dumps({k: v for k, v in story.items() if k != "orientation"})
