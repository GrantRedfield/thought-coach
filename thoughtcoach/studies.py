"""Lichess study chapters to go deeper on a story card.

study_links.json lists explained positions from popular teaching studies on lichess (endgame
courses, thinking and middlegame courses), with each chapter's link, study, name and author. A card
gets a chapter when it teaches the same thing: an ending of the same kind (rook ending, pawn ending,
...), or, in the middlegame, the habit the card is about (weaknesses, the worst piece, the
opponent's ideas, candidate moves, converting a winning position). Among those, the chapter whose
pawns look most like the game's wins. Only links and credit are kept: the author explains it there.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import chess

DATA = Path(__file__).with_name("study_links.json")
MAX_LINKS = 3

# which habit a card is about, for middlegame positions
THEME = {"weak_made": "weakness", "weak_found": "weakness", "weak_missed": "weakness",
         "worst": "worst_piece", "worst_named": "worst_piece",
         "threat": "opponent", "threat_seen": "opponent", "trust": "candidates"}
# (turning points and rushed moves have no one lesson: a chapter picked for them was too often off-topic)
PIECE_COURSE = {chess.KNIGHT: "Knights", chess.BISHOP: "Bishops", chess.ROOK: "Rooks"}  # for a stuck piece
CONVERT = "How to Win Winning Positions"  # for "caught" cards: a winning position that slipped


def ending(board: chess.Board) -> str | None:
    """The kind of ending on the board, or None in the middlegame."""
    kinds ={board.piece_type_at(sq) for sq in chess.SQUARES} - {None, chess.KING, chess.PAWN}
    if not kinds:
        return "pawn"
    worth = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    total = sum(worth[p.piece_type] for p in board.piece_map().values() if p.piece_type in worth)
    if kinds == {chess.QUEEN} and total <= 18:
        return "queen"
    if chess.QUEEN in kinds or total > 16:
        return None
    if kinds == {chess.ROOK}:
        return "rook"
    if kinds <= {chess.KNIGHT, chess.BISHOP}:
        return "minor"
    return "rook+minor"


def pawns(board: chess.Board) -> list[str]:
    """White's and Black's pawns as two hex bitboards."""
    return [format(int(board.pieces(chess.PAWN, c)), "x") for c in (chess.WHITE, chess.BLACK)]


def _likeness(a: list[str], b: list[str]) -> float:
    """How alike two pawn structures are (0-1), either way round (a structure and its mirror for the
    other colour count the same)."""
    aw, ab = int(a[0], 16), int(a[1], 16)
    best = 0.0
    for bw, bb in ((int(b[0], 16), int(b[1], 16)),
                   (chess.flip_vertical(int(b[1], 16)), chess.flip_vertical(int(b[0], 16)))):
        same = bin(aw & bw).count("1") + bin(ab & bb).count("1")
        either = bin(aw | bw).count("1") + bin(ab | bb).count("1")
        best = max(best, same / either if either else 1.0)
    return best


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA.exists():
        return {"chapters": [], "positions": []}
    return json.loads(DATA.read_text(encoding="utf-8"))


def chapter_for(kind: str, board: chess.Board, used: set[int] = frozenset(),
                piece: int | None = None) -> dict | None:
    """The study chapter for a card of this kind on this position, or None if nothing fits. `piece`:
    the card's piece (a stuck knight, bishop, rook), which picks that piece's course when there is one."""
    d = _data()
    end = ending(board)
    if end:
        pool = [p for p in d["positions"] if p["course"] == "endgame" and p["ending"] == end]
        why = {"pawn": "pawn endings", "rook": "rook endings", "minor": "minor-piece endings",
               "queen": "queen endings", "rook+minor": "endings with a rook and a minor piece"}[end]
    elif kind == "caught":
        pool = [p for p in d["positions"] if d["chapters"][p["c"]]["study"] == CONVERT]
        why = "converting a winning position"
    elif kind in THEME:
        theme = THEME[kind]
        pool = [p for p in d["positions"] if p["course"] == "middlegame" and theme in p["themes"]
                and "opening" not in d["chapters"][p["c"]]["chapter"].lower()]
        course = PIECE_COURSE.get(piece)
        if course and any(d["chapters"][p["c"]]["study"].startswith(course) for p in pool):
            pool = [p for p in pool if d["chapters"][p["c"]]["study"].startswith(course)]
        why = {"weakness": "weaknesses", "worst_piece": "improving your worst piece",
               "opponent": "the opponent's ideas", "candidates": "comparing candidate moves"}[theme]
    else:
        return None
    pool = [p for p in pool if p["c"] not in used]
    if not pool:
        return None
    mine = pawns(board)
    best = max(pool, key=lambda p: _likeness(mine, p["pawns"]))
    c = d["chapters"][best["c"]]
    return {"i": best["c"], "url": c["url"], "study": c["study"], "chapter": c["chapter"], "by": c["by"],
            "about": why, "like": round(_likeness(mine, best["pawns"]), 2)}


def attach(beats: list[dict], board_at) -> None:
    """Give up to MAX_LINKS cards a study chapter (endings first, then the closest pawn structures).
    board_at(beat) is the card's position, or None."""
    found = []
    used: set[int] = set()
    for b in beats:
        board = board_at(b)
        if board is None:
            continue
        sq = next(iter(b.get("fill") or {}), None)
        piece = board.piece_type_at(int(sq)) if sq is not None and b["kind"] in ("worst", "worst_named") else None
        ch = chapter_for(b["kind"], board, used, piece)
        if ch:
            found.append((b, board, piece, ending(board) is not None, ch["like"]))
    found.sort(key=lambda x: (not x[3], -x[4]))
    for b, board, piece, _, _ in found:
        if len(used) >= MAX_LINKS:
            break
        ch = chapter_for(b["kind"], board, used, piece)  # the best chapter not linked yet
        if ch:
            used.add(ch["i"])
            b["study"] = {k: ch[k] for k in ("url", "study", "chapter", "by", "about")}
