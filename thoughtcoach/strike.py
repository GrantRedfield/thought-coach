"""Who struck first: the moment a balanced game breaks open into tactics.

Read from the moves actually played (no extra engine work; the win chances come from the review):

  A forcing sequence is a run of forcing moves: captures, checks, and moves that create a real
  threat (what that side could win if it could move again, the null-move threat, is worth THREAT
  or more: 15. g4 hitting the knight on f5). The threat has to come from the move itself: made
  by the piece that moved, or by a piece it uncovered. A quiet move while some other threat is
  hanging in the air (12... a5 with ...dxe4 possible since 11. e4) is not forcing. It starts with one that isn't a recapture (a capture
  on the square just captured on is an exchange in progress) and ends after two quiet moves in a
  row, or MAX_PLIES. Their threats come from the review (the threat in your next position, if it
  is new); yours take one short search after each of your moves (`threats`, from an engine).

  The first strike is the first forcing sequence that starts from a balanced game (your win
  chance between BALANCED and 1 - BALANCED before it, and before the move just before it) and
  matters: the win chance moves at least SWING over it, or someone comes out at least MATERIAL
  pawns ahead. Even trades are not strikes.

  The side that plays its first move struck first. If the move just before it (by the other
  side) lost DOOR or more, that move opened the door: the strike was punishing it. The outcome
  is measured from before the door (or the strike) to the end of the sequence, from the
  striker's side: up at least OUTCOME = it worked, down at least OUTCOME = it backfired,
  otherwise nothing changed. If the game ended inside the sequence, the result counts.

The first break is separate (`first_break`): the first pawn move from move BREAK_FROM on that
puts a pawn up against an enemy pawn (8. d4 against a pawn on c5), with the trades that follow
and the win chance before and after. It is who opened the position, which is often not who
struck first: a break whose trades are even changes nothing a strike would count.
"""

from __future__ import annotations

import chess
import chess.pgn

from .fmt import pct

BALANCED = 0.20
SWING = 0.15
MATERIAL = 2
DOOR = 0.08
OUTCOME = 0.10
MAX_PLIES = 10
THREAT = 0.15
BREAK_FROM = 6  # move number: pawn tension before this is the opening (1. d4 d5 2. c4)
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def _balance(b: chess.Board, color: chess.Color) -> int:
    return sum(VALUE[p.piece_type] * (1 if p.color == color else -1) for p in b.piece_map().values())


def _label(ply: int, san: str) -> str:
    """'19... b5' / '20. Rxd8+' for the move that is ply number `ply` (1 = White's first)."""
    n = (ply + 1) // 2
    return f"{n}. {san}" if ply % 2 else f"{n}... {san}"


def threats(engine, game: chess.pgn.Game, analysis: dict, me: chess.Color, wp_model: str = "lichess") -> dict:
    """{ply: (swing, threat SAN)} for the moves that create a threat: theirs from the review's
    null-move threat in your next position (when it is a new one), yours from a search after each
    of your moves with you to move again. `engine` may be None (then only theirs)."""
    from .engine import win_prob

    out: dict[int, tuple[float, str]] = {}
    records = sorted((r for r in analysis["moves"] if not r.get("skipped")), key=lambda r: r["ply"])
    moves = list(game.mainline_moves())
    boards = [game.board()]
    for mv in moves:
        b = boards[-1].copy(stack=False)
        b.push(mv)
        boards.append(b)
    prev = None
    for r in records:
        t = r.get("threat") or {}
        p = r["ply"] - 1  # their move just before your position
        if (t.get("swing", 0) >= THREAT and (not prev or prev.get("move_san") != t.get("move_san"))
                and 1 <= p <= len(moves) and made_by(boards[p], moves[p - 1], t.get("move_san"))):
            out[p] = (t["swing"], t["move_san"])
        prev = t
    if engine is None:
        return out
    for r in records:
        if r.get("wp_after") is None or r["ply"] >= len(boards):
            continue
        after = boards[r["ply"]]
        if after.is_check() or after.is_game_over():
            continue
        passed = after.copy(stack=False)
        passed.push(chess.Move.null())
        passed = chess.Board(passed.fen())
        info = engine.analyse(passed)[0]
        if info.get("pv"):
            # A new threat: moving again is worth more than your best move was before this one
            # (castling with ...b5 already possible creates nothing).
            swing = win_prob(info, me, wp_model) - max(r["wp_after"], r.get("wp_before") or 0)
            if swing >= THREAT and made_by(after, moves[r["ply"] - 1], passed.san(info["pv"][0])):
                out[r["ply"]] = (round(swing, 3), passed.san(info["pv"][0]))
    return out


def made_by(after: chess.Board, move: chess.Move, threat_san: str | None) -> bool:
    """Does `move` (already played: `after` is the position it led to) make this threat? The threat
    (the mover's move if they could go again) has to be played by the piece that moved, or run
    through the square it left (a discovered attack)."""
    if not threat_san:
        return False
    again = after.copy(stack=False)
    again.push(chess.Move.null())
    try:
        t = again.parse_san(threat_san)
    except ValueError:
        return False
    return t.from_square == move.to_square or move.from_square in chess.SquareSet.between(t.from_square, t.to_square)


def find(game: chess.pgn.Game, analysis: dict, me: chess.Color, threat: dict | None = None) -> dict:
    """The first strike, or {"side": None, "text": why there was none}. `threat`: from threats()."""
    threat = threat or {}
    # Your win chance after each ply (0 = the start), from the review's records.
    wp: dict[int, float] = {}
    for r in analysis["moves"]:
        if r.get("skipped") or r.get("wp_before") is None:
            continue
        wp[r["ply"] - 1] = r["wp_before"]
        if r.get("wp_after") is not None:
            wp[r["ply"]] = r["wp_after"]
    moves = list(game.mainline_moves())
    boards = [game.board()]
    for m in moves:
        b = boards[-1].copy(stack=False)
        b.push(m)
        boards.append(b)
    n = len(moves)
    result = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}.get(game.headers.get("Result", "*"))
    end_of_game = None if result is None else (result if me == chess.WHITE else 1 - result)

    def forcing(i: int) -> bool:  # ply i (1-based)
        b, m = boards[i - 1], moves[i - 1]
        return b.is_capture(m) or boards[i].is_check() or i in threat

    def blunder(i: int) -> bool:
        """A quiet threat-making move that loses DOOR or more for the side playing it: a mistake, not a strike."""
        if i not in threat or boards[i - 1].is_capture(moves[i - 1]) or boards[i].is_check():
            return False
        if (i - 1) not in wp or i not in wp:
            return False
        mover_is_me = boards[i - 1].turn == me
        return (wp[i - 1] - wp[i]) * (1 if mover_is_me else -1) >= DOOR

    def recapture(i: int) -> bool:
        return i > 1 and boards[i - 2].is_capture(moves[i - 2]) and moves[i - 1].to_square == moves[i - 2].to_square

    def wp_at(i: int) -> float | None:
        for k in (i, i + 1, i + 2):
            if k in wp:
                return wp[k]
        return end_of_game if i >= n else None

    i = min(wp) + 1 if wp else n + 1
    while i <= n:
        if not forcing(i) or recapture(i) or blunder(i):
            i += 1
            continue
        end, k, quiet = i, i, 0
        while k < n and quiet < 2 and k - i < MAX_PLIES:
            k += 1
            if forcing(k):
                end, quiet = k, 0
            else:
                quiet += 1
        before = wp.get(i - 1)
        ref_ply = i - 2 if (i - 2) in wp else i - 1
        ref = wp.get(ref_ply)
        if before is None or ref is None:
            i = end + 1
            continue
        if not (BALANCED <= ref <= 1 - BALANCED):
            return _no_strike(wp, boards, moves, me)  # decided before any tactics started
        striker = boards[i - 1].turn
        after = wp_at(end) if end < n else end_of_game
        if after is None:
            after = before
        sign = 1 if striker == me else -1
        change = (after - ref) * sign  # from the striker's side
        material = _balance(boards[end], striker) - _balance(boards[i - 1], striker)
        if abs(after - before) >= SWING or abs(after - ref) >= SWING or abs(material) >= MATERIAL:
            if not (BALANCED <= before <= 1 - BALANCED):
                # The move just before already decided it (a blunder): this only cashes it in.
                mine = striker != me
                door = _label(i - 1, boards[i - 2].san(moves[i - 2]))
                loss = abs(wp[i - 2] - wp[i - 1]) if (i - 2) in wp else None
                size = "" if loss is None else f" ({pct(-loss)})" if mine else f" ({'+' + pct(loss)} for you)"
                return {"side": None, "ply": i - 1, "decided_by": door, "decided_side": "you" if mine else "them",
                        "text": f"None: the game was decided by {'your' if mine else 'their'} {door}{size}, "
                                f"cashed in with {_label(i, boards[i - 1].san(moves[i - 1]))}."}
            door = None
            if ref_ply == i - 2 and i >= 2:
                loss = (wp[i - 2] - wp[i - 1]) * (1 if striker != me else -1)  # lost by the other side
                if loss >= DOOR:
                    door = {"ply": i - 1, "move": _label(i - 1, boards[i - 2].san(moves[i - 2])),
                            "side": "you" if striker != me else "them", "loss": round(loss, 3)}
            outcome = "worked" if change >= OUTCOME else "backfired" if change <= -OUTCOME else "even"
            line = boards[i - 1].variation_san(moves[i - 1:end])
            out = {"ply": i, "move_number": (i + 1) // 2, "side": "you" if striker == me else "them",
                   "move": _label(i, boards[i - 1].san(moves[i - 1])), "line": line, "door": door,
                   "outcome": outcome, "wp_from": round(ref, 3), "wp_to": round(after, 3),
                   "material": material, "game_ended": end >= n,
                   "threat": threat[i][1] if i in threat and not boards[i - 1].is_capture(moves[i - 1]) else None}
            out["text"] = describe(out)
            return out
        i = end + 1
    return _no_strike(wp, boards, moves, me)


def first_break(game: chess.pgn.Game, analysis: dict, me: chess.Color) -> dict | None:
    """The first pawn break: {"ply", "move", "side", "centre", "line", "end", "wp_from", "wp_to", "text"}."""
    wp: dict[int, float] = {}
    for r in analysis["moves"]:
        if r.get("skipped") or r.get("wp_before") is None:
            continue
        wp.setdefault(r["ply"] - 1, r["wp_before"])
        if r.get("wp_after") is not None:
            wp[r["ply"]] = r["wp_after"]
    moves = list(game.mainline_moves())
    b = game.board()
    boards = [b.copy(stack=False)]
    for m in moves:
        b.push(m)
        boards.append(b.copy(stack=False))
    for i in range(2 * BREAK_FROM - 1, len(moves) + 1):
        before, m, after = boards[i - 1], moves[i - 1], boards[i]
        if before.piece_type_at(m.from_square) != chess.PAWN or before.is_capture(m):
            continue
        mover = before.turn
        to = m.to_square
        enemy_pawn = chess.Piece(chess.PAWN, not mover)
        hits = any(after.piece_at(s) == enemy_pawn for s in after.attacks(to))
        hit = any(after.piece_at(s) == enemy_pawn for s in after.attackers(not mover, to))
        if not (hits or hit):
            continue
        # The trades that follow: captures (one quiet move in between allowed).
        end, k, quiet = i, i, 0
        while k < len(moves) and quiet < 2 and k - i < MAX_PLIES:
            k += 1
            if boards[k - 1].is_capture(moves[k - 1]):
                end, quiet = k, 0
            else:
                quiet += 1
        w0 = next((wp[p] for p in range(i - 1, end + 1) if p in wp), None)
        w1 = next((wp[p] for p in range(end, end + 3) if p in wp), None)
        if w1 is None:
            w1 = next((wp[p] for p in range(end - 1, i - 2, -1) if p in wp), None)
        out = {"ply": i, "move": _label(i, before.san(m)), "side": "you" if mover == me else "them",
               "centre": chess.square_file(to) in (3, 4), "end": end,
               "line": boards[i].variation_san(moves[i:end]).replace("...", "... ") if end > i else "",
               "wp_from": None if w0 is None else round(w0, 3), "wp_to": None if w1 is None else round(w1, 3)}
        out["text"] = describe_break(out)
        return out
    return None


def describe_break(br: dict) -> str:
    """'You opened the centre with 8. d4; after 8... cxd4 9. Qxd4 the balance held (55% -> 54%).'"""
    who = "You" if br["side"] == "you" else "They"
    what = "opened the centre" if br["centre"] else "made the first pawn break"
    head = f"{who} {what} with {br['move']}"
    if br["wp_from"] is None or br["wp_to"] is None:
        return f"{head}."
    after = f"after {br['line']} " if br["line"] else ""
    wp = f"({pct(br['wp_from'])} → {pct(br['wp_to'])})"
    d = (br["wp_to"] - br["wp_from"]) * (1 if br["side"] == "you" else -1)
    how = "it paid off" if d >= OUTCOME else "it backfired" if d <= -OUTCOME else "the balance held"
    return f"{head}; {after}{how} {wp}."


def _no_strike(wp: dict, boards: list, moves: list, me: chess.Color) -> dict:
    """No first strike: say when the game left balance without one, if it did."""
    for ply in sorted(wp):
        if ply and not BALANCED <= wp[ply] <= 1 - BALANCED:
            mover = boards[ply - 1].turn
            move = _label(ply, boards[ply - 1].san(moves[ply - 1]))
            whose = "your" if mover == me else "their"
            return {"side": None, "ply": ply, "text": f"None: the balance broke without tactics, "
                    f"by quiet moves (after {whose} {move} your win chance was {pct(wp[ply])})."}
    return {"side": None, "ply": None, "text": "None: the game never broke open into tactics."}


def describe(s: dict) -> str:
    """One sentence, e.g. 'They struck first with 20. Rxd8+, after your 19... b5 opened the door:
    your win chance 50% -> 0%.'"""
    wp = f"your win chance {pct(s['wp_from'])} → {pct(s['wp_to'])}"
    door = s.get("door")
    hits = f" (threatening {s['threat']})" if s.get("threat") else ""
    if s["side"] == "them":
        head = f"They struck first with {s['move']}{hits}"
        if door and s["outcome"] == "worked":
            return f"{head}, after your {door['move']} opened the door: {wp}."
        if door:
            return f"{head} after your {door['move']}, but you held: {wp}."
        if s["outcome"] == "worked":
            return f"{head}, and it worked: {wp}."
        if s["outcome"] == "backfired":
            return f"{head}, and you came out on top: {wp}."
        return f"{head}; you held: {wp}."
    head = f"You struck first with {s['move']}{hits}"
    if door and s["outcome"] == "worked":
        return f"{head}, punishing their {door['move']}: {wp}."
    if door:
        return (f"{head}: their {door['move']} gave you the chance, but it "
                f"{'backfired' if s['outcome'] == 'backfired' else 'came out even'}: {wp}.")
    if s["outcome"] == "worked":
        return f"{head}, and it worked: {wp}."
    if s["outcome"] == "backfired":
        return f"{head}, and it backfired: {wp}."
    return f"{head}; it came out even: {wp}."
