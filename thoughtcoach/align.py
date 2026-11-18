"""Milestone 3: align an arrow recording with the game's PGN into one thinking window per move.

A thinking window belongs to one of my moves. It covers:
  - my own time: from the moment the opponent's move appeared on the board until my move appeared
  - the opponent's time just before it: from my previous move until the opponent replied
    (arrows drawn then are thoughts about this move too, made before the position existed)

Every arrow/circle is placed by the board position on screen when it was drawn (recorded as a FEN
placement, matched to the PGN), not by timestamps alone, so a skipped position (premove) or clock
lag cannot shift it to the wrong move.

Shapes are classified by what stands on the arrow's starting square in the position it was drawn in:
  candidate       arrow starting on one of my pieces
  opponent_idea   arrow starting on one of the opponent's pieces
  route           arrow starting on an empty square (e.g. a plan's second step)
  key_square      circle
"""

from __future__ import annotations

import chess
import chess.pgn

from .analyse import parse_time_control


# A circle on an arrow's destination drawn this soon after the arrow is part of that arrow
# (a drawing habit), not a separate key square.
FOLD_CIRCLE_MS = 1000
STALE_SELECTION_MS = 1000  # a "pickup" of the square my move just landed on, this soon after, is the highlight lingering


def fold_circles(shapes: list[dict]) -> list[dict]:
    """Merge 'arrow, then a circle on its target square' into the arrow (target_circled=True)."""
    kept: list[dict] = []
    for shape in shapes:
        if shape["kind"] == "key_square" and shape.get("source", "arrow") == "arrow":
            arrow = next(
                (
                    a
                    for a in reversed(kept)
                    if a["to"] == shape["from"]
                    and a.get("source", "arrow") == "arrow"
                    and not a.get("target_circled")
                    and 0 <= shape["drawn_t"] - a["drawn_t"] <= FOLD_CIRCLE_MS
                ),
                None,
            )
            if arrow is not None:
                arrow["target_circled"] = True
                continue
        kept.append(shape)
    return kept


def match_positions(events: list[dict], placements: list[str]) -> tuple[dict[int, float], list[int | None]]:
    """Map recorded `position` events to PGN plies.

    Returns (first time each ply's position was seen, ply on screen at each event index).
    Matching moves forward through the game, so repeated positions are matched in order.
    A position that doesn't occur later in the game (e.g. a takeback) keeps the previous ply.
    """
    seen: dict[int, float] = {}
    on_screen: list[int | None] = []
    current: int | None = None
    for event in events:
        if event["type"] == "position":
            start = 0 if current is None else current
            for ply in range(start, len(placements)):
                if placements[ply] == event["placement"]:
                    current = ply
                    seen.setdefault(ply, event["t"])
                    break
        on_screen.append(current)
    return seen, on_screen


def classify(piece: chess.Piece | None, is_arrow: bool, me: chess.Color) -> str:
    if not is_arrow:
        return "key_square"
    if piece is None:
        return "route"
    return "candidate" if piece.color == me else "opponent_idea"


def as_move(board: chess.Board, frm: str, to: str) -> chess.Move | None:
    """The legal move from `frm` to `to` in `board` (promoting to a queen), or None."""
    move = chess.Move(chess.parse_square(frm), chess.parse_square(to))
    piece = board.piece_at(move.from_square)
    if piece and piece.piece_type == chess.PAWN and chess.square_rank(move.to_square) in (0, 7):
        move.promotion = chess.QUEEN
    return move if board.is_legal(move) else None


def time_spent_by_ply(game: chess.pgn.Game) -> dict[int, float | None]:
    """Seconds each move took according to the PGN %clk comments (ply -> seconds)."""
    base, increment = parse_time_control(game.headers.get("TimeControl"))
    clock = {chess.WHITE: base, chess.BLACK: base}
    spent: dict[int, float | None] = {}
    board = game.board()
    for ply, node in enumerate(game.mainline(), start=1):
        mover = board.turn
        after = node.clock()
        before = clock[mover]
        spent[ply] = None if after is None or before is None else max(0.0, before - after + increment)
        if after is not None:
            clock[mover] = after
        board.push(node.move)
    return spent


def with_turn(board: chess.Board, color: chess.Color) -> chess.Board | None:
    """`board` with `color` to move (via a null move if needed), or None if that's impossible (check)."""
    b = board.copy(stack=False)
    if b.turn != color:
        if b.is_check():
            return None
        b.push(chess.Move.null())
    return b


def spoken_moves(board: chess.Board, m: dict) -> list[chess.Move]:
    """Legal moves in `board` that fit a spoken mention (piece, from, to, takes, castles)."""
    found = []
    for move in board.legal_moves:
        if move.promotion not in (None, chess.QUEEN):
            continue
        if m.get("castle"):
            if not board.is_castling(move):
                continue
            kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
            if kingside != (m["castle"] == "K"):
                continue
        else:
            if board.is_castling(move) and m.get("piece") != "K":
                continue
            if m.get("piece") and chess.piece_symbol(board.piece_type_at(move.from_square)).upper() != m["piece"]:
                continue
            if m.get("from") and move.from_square != chess.parse_square(m["from"]):
                continue
            if m.get("from_file") and chess.square_file(move.from_square) != "abcdefgh".index(m["from_file"]):
                continue
            if m.get("to") and move.to_square != chess.parse_square(m["to"]):
                continue
            if not m.get("to") and not m.get("from"):
                continue
            if m.get("capture") and not board.is_capture(move):
                continue
        found.append(move)
    return found


def resolve_voice(event: dict, me: chess.Color, drawn_board: chess.Board, decision_board: chess.Board,
                  played: chess.Move | None = None) -> dict:
    """Turn a spoken mention into a thought like an arrow: kind, from/to squares, move SAN, legality.

    `played` is the move I then made; an ambiguous mention ("rook e1" with two rooks) that fits it is taken
    to mean it.
    """
    tag = event["tag"]
    out = {"kind": tag, "from": None, "to": None, "move_san": None, "legal": None, "ambiguous": []}
    if tag == "worst_piece":
        out["from"] = event.get("to") or event.get("from")  # "the bishop on c1"
        return out
    if event.get("piece_only"):  # a bare "rook": not a move, and nothing to show
        return {**out, "kind": "other"}
    if tag == "weakness" or event.get("square_only"):
        out.update(kind="key_square", **{"from": event.get("to")})
        return out if out["from"] else {**out, "kind": "other"}
    if tag not in ("candidate", "opponent_idea"):
        return {**out, "kind": "other"}

    def attempt(kind: str):
        side = me if kind == "candidate" else not me
        # Candidates: in the position I have to move in; ideas: in the position on screen.
        boards = [decision_board, with_turn(drawn_board, me)] if kind == "candidate" else [with_turn(drawn_board, not me)]
        for i, board in enumerate(boards):
            if board is None or board.turn != side:
                continue
            moves = spoken_moves(board, event)
            if len(moves) > 1 and i == 0 and played in moves:
                moves = [played]
            if len(moves) == 1:
                return {"kind": kind, "from": chess.square_name(moves[0].from_square),
                        "to": chess.square_name(moves[0].to_square), "move_san": board.san(moves[0]),
                        "legal": i == 0, "ambiguous": []}
            if len(moves) > 1:
                return {"kind": kind, "from": None, "to": None, "move_san": None, "legal": None,
                        "ambiguous": sorted(board.san(mv) for mv in moves)}
        return None

    # A move only the other side can make ("rook e8" while I have no rook that reaches e8) is theirs.
    other = "opponent_idea" if tag == "candidate" else "candidate"
    result = attempt(tag) or attempt(other)
    # Speech that fits no legal move for either side ("a1 to h8 diagonal", a misheard square) stays in the
    # transcript but is not reported as a move.
    return result or {**out, "kind": "other"}


def align(game: chess.pgn.Game, recording: dict, me: chess.Color, voice: list[dict] | None = None) -> dict:
    """One thinking window per move of mine, with the shapes drawn (and things said) for it.

    `voice` is an optional list of `voice` events from voice.voice_events(), merged in by time.
    """
    if recording.get("format") != "thoughtcoach-arrows/2":
        raise ValueError("alignment needs a recording from userscript 0.3.0 or later (format 2)")

    # Positions of the game: boards[p] is the position after p plies.
    boards = [game.board()]
    moves: list[chess.Move] = []
    for move in game.mainline_moves():
        moves.append(move)
        board = boards[-1].copy(stack=False)
        board.push(move)
        boards.append(board)
    placements = [b.board_fen() for b in boards]
    n_plies = len(moves)

    # Spoken mentions go in by time; sorting is stable, so recorded events keep their order.
    events = sorted(recording["events"] + list(voice or []), key=lambda e: e["t"])
    seen, on_screen = match_positions(events, placements)
    spent = time_spent_by_ply(game)

    # Each shape_add (with when it was removed again) and each spoken mention.
    shapes_by_ply: dict[int, list[dict]] = {}
    for index, event in enumerate(events):
        if event["type"] not in ("shape_add", "voice", "pick_up"):
            continue
        drawn_in = on_screen[index]
        if drawn_in is None:
            continue
        own_turn = boards[drawn_in].turn == me
        my_ply = drawn_in + 1 if own_turn else drawn_in + 2  # the move of mine this thought is about
        if my_ply > n_plies:
            continue
        if event["type"] == "pick_up":
            # A piece of mine I picked up: did I end up moving that piece?
            square = chess.parse_square(event["square"])
            piece = boards[drawn_in].piece_at(square)
            if piece is None or piece.color != me:
                continue
            # Lichess can leave the square my move just landed on highlighted: that is not a pickup.
            last_position_t = next(
                (e["t"] for e in reversed(events[:index]) if e["type"] == "position"), None
            )
            if (
                drawn_in > 0
                and boards[drawn_in - 1].turn == me
                and moves[drawn_in - 1].to_square == square
                and last_position_t is not None
                and event["t"] - last_position_t < STALE_SELECTION_MS
            ):
                continue
            # Held until let go, or until the board changes (the highlight can linger past a move).
            put_down = next(
                (e["t"] for e in events[index + 1 :] if e["type"] in ("put_down", "position")), None
            )
            shapes_by_ply.setdefault(my_ply, []).append({
                "kind": "picked_up",
                "source": "pickup",
                "from": event["square"],
                "to": None,
                "piece": piece.symbol().upper(),
                "phase": "own_time" if own_turn else "opponent_time",
                "drawn_t": event["t"],
                "on_board_s": None if put_down is None else round((put_down - event["t"]) / 1000, 1),
                "move_san": None,
                "legal": None,
                "played": moves[my_ply - 1].from_square == chess.parse_square(event["square"]),
                "brush": None,
                "target_circled": False,
            })
            continue
        if event["type"] == "voice":
            spoken = resolve_voice(event, me, boards[drawn_in], boards[my_ply - 1], moves[my_ply - 1])
            if spoken["kind"] == "other":
                continue
            shape = {
                **spoken,
                "source": "voice",
                "said": event["said"],
                "utterance": event["utterance"],
                "tagger": event["tagger"],
                "phase": "own_time" if own_turn else "opponent_time",
                "drawn_t": event["t"],
                "on_board_s": None,
                "brush": None,
                "played": spoken["move_san"] is not None
                and spoken["kind"] == "candidate"
                and spoken["legal"]
                and boards[my_ply - 1].parse_san(spoken["move_san"]) == moves[my_ply - 1],
                "target_circled": False,
            }
            shapes_by_ply.setdefault(my_ply, []).append(shape)
            continue
        removed = next(
            (
                e["t"]
                for e in events[index + 1 :]
                if e["type"] == "shape_remove" and (e["from"], e["to"]) == (event["from"], event["to"])
            ),
            None,
        )
        drawn_board = boards[drawn_in]
        piece = drawn_board.piece_at(chess.parse_square(event["from"]))
        kind = classify(piece, event["to"] is not None, me)

        # Read the arrow as a move where that makes sense.
        decision_board = boards[my_ply - 1]  # the position I had to move in
        move, san = None, None
        if kind == "candidate":
            move = as_move(decision_board, event["from"], event["to"])
            san = decision_board.san(move) if move else None
        elif kind == "opponent_idea":
            # The opponent's move if it were their turn: in the position it was drawn in if the
            # opponent was to move, otherwise in that position with a null move (as in Milestone 1).
            board = drawn_board.copy(stack=False)
            if board.turn == me and not board.is_check():
                board.push(chess.Move.null())
            move = as_move(board, event["from"], event["to"]) if board.turn != me else None
            san = board.san(move) if move else None
        shape = {
            "kind": kind,
            "source": "arrow",
            "from": event["from"],
            "to": event["to"],
            "brush": event["brush"],
            "phase": "own_time" if own_turn else "opponent_time",
            "drawn_t": event["t"],
            "on_board_s": None if removed is None else round((removed - event["t"]) / 1000, 1),
            "move_san": None,
            "legal": None,
            "played": None,
            "target_circled": False,
        }
        if kind in ("candidate", "opponent_idea"):
            shape["legal"] = move is not None
            shape["move_san"] = san
        if kind == "candidate":
            shape["played"] = move == moves[my_ply - 1]
        shapes_by_ply.setdefault(my_ply, []).append(shape)

    windows = []
    for ply in range(1, n_plies + 1):
        before = boards[ply - 1]
        if before.turn != me:
            continue
        start = seen.get(ply - 1)  # opponent's move appeared
        end = seen.get(ply)  # my move appeared
        opp_start = seen.get(ply - 2)  # my previous move appeared (opponent's time began)
        shapes = fold_circles(shapes_by_ply.get(ply, []))
        for s in shapes:
            ref = start if start is not None else end
            drawn_t = s.pop("drawn_t")
            s["drawn_s"] = None if ref is None else round((drawn_t - ref) / 1000, 1)
        windows.append(
            {
                "ply": ply,
                "move_number": before.fullmove_number,
                "fen": before.fen(),
                "move_san": before.san(moves[ply - 1]),
                "move_uci": moves[ply - 1].uci(),
                "time_spent_pgn": None if spent.get(ply) is None else round(spent[ply], 1),
                "own_time_s": None if start is None or end is None else round((end - start) / 1000, 1),
                "opponent_time_s": None if opp_start is None or start is None else round((start - opp_start) / 1000, 1),
                "position_seen": start is not None,
                "shapes": shapes,
            }
        )

    return {
        "game_id": recording["game_id"],
        "my_color": "white" if me == chess.WHITE else "black",
        "plies": n_plies,
        "positions_matched": len(seen),
        "positions_missed": [p for p in range(n_plies + 1) if p not in seen],
        "windows": windows,
    }


# ---------------------------------------------------------------- report

KIND_LABEL = {
    "candidate": "candidate",
    "opponent_idea": "opp. idea",
    "route": "route",
    "key_square": "key square",
    "worst_piece": "worst piece",
    "picked_up": "picked up",
}


def _secs(x: float | None) -> str:
    return "  -  " if x is None else f"{x:5.1f}s"


def render(result: dict, headers: dict | None = None) -> str:
    h = headers or {}
    out = [
        "THOUGHT COACH - thinking windows",
        f"Game {result['game_id']}: {h.get('White', '?')} vs {h.get('Black', '?')}  {h.get('Result', '')}"
        f"  TC {h.get('TimeControl', '?')}   you: {result['my_color']}",
        f"Positions matched to the PGN: {result['positions_matched']} of {result['plies'] + 1}"
        + (f" (not seen: plies {result['positions_missed']})" if result["positions_missed"] else ""),
        "",
        "own = time from the opponent's move to yours (recording); opp = the opponent's thinking time before it.",
        "at = seconds after the opponent's move appeared (negative = during the opponent's time).",
        "o after an arrow = you circled its target right after drawing it (counted as one shape).",
        'said "..." = from your voice; the rest are arrows/circles.',
        "",
    ]
    with_thoughts = [w for w in result["windows"] if w["shapes"]]
    out.append(f"Moves with recorded thoughts: {len(with_thoughts)} of {len(result['windows'])}")
    out.append("")
    dots = "." if result["my_color"] == "white" else "..."
    for w in result["windows"]:
        label = f"{w['move_number']}{dots} {w['move_san']}"
        out.append(
            f"  {label:<14} own {_secs(w['own_time_s'])}  opp {_secs(w['opponent_time_s'])}"
            f"  (PGN clock: {_secs(w['time_spent_pgn'])})"
        )
        for s in w["shapes"]:
            what = f"{s['from']}->{s['to']}" if s["to"] else (s["from"] or "")
            move = ""
            if s["move_san"]:
                move = f" = {s['move_san']}" + (" (played)" if s["played"] else "")
                if s["legal"] is False:
                    move += " (not legal by then)"
            elif s.get("ambiguous"):
                move = f" = one of {'/'.join(s['ambiguous'])}"
            elif s["legal"] is False:
                move = " (not a legal move here)"
            if s["kind"] == "picked_up":
                what = f"{s['piece']}{s['from']}"
                move = " (moved it)" if s["played"] else " (not moved)"
            if s.get("source") == "voice":
                what = f'said "{s["said"]}"' + (f" {what}" if what and not s["move_san"] else "")
            if s.get("target_circled"):
                what += "o"  # arrow with its target square circled
            shown = "" if s["on_board_s"] is None else f", shown {s['on_board_s']}s"
            drawn = "?" if s["drawn_s"] is None else f"{s['drawn_s']:+.1f}s"
            out.append(f"      {KIND_LABEL[s['kind']]:<11} {what:<7}{move:<26} at {drawn}{shown}")
    return "\n".join(out)
