"""Aagaard's first two questions, checked by the engine: where are the weaknesses, and which is
the worst piece? (The third, the opponent's idea, is the null-move threat in analyse.py.)

Stockfish doesn't name weaknesses or bad pieces, so each gets a definition the engine can back:

Weaknesses: something the engine's main line uses within its first PV_PLIES plies:
  - a weak pawn (isolated, doubled or backward) that the other side captures, as a gain: taking
    back material in an exchange (10...Nxe5 after dxe5) is a recapture, not using a weakness;
  - a hole (a square the side's pawns can never cover again; not on the a/h-files) where an enemy
    piece settles: it goes there without capturing a piece, and isn't taken straight back;
  - a loose piece (knight, bishop, rook or queen) that the main line wins: captured while undefended,
    not as a recapture, not taken back, and the line doesn't end with less material. (Winning the bishop on a
    hole is this, not using the hole.) Pieces the opponent's threat line would win are
    threats, question 3, not weaknesses.
  Your own weaknesses are also taken from the opponent's threat line (their idea if you passed).
  A weakness the engine never goes near is not counted, and none are counted once the game is
  decided (your win chance under DECIDED or over 1 - DECIDED).

  Weaknesses your own move creates: after each move of yours that cost CREATED_MIN_LOSS or more,
  your weaknesses that the opponent's best reply line (from the review) uses, and that weren't
  there before the move: the pawn wasn't weak, the square wasn't a hole, the piece was defended
  (19...b5 taking the b6 pawn's guard off the knight on c5). Marked `created_by` your move.

Worst piece (only while the game is in the balance, your win chance between SLOW and 1 - SLOW:
  when you're well behind or ahead, concrete play matters more): your least active knight, bishop, rook or queen (the squares it reaches that aren't
  your own pieces or covered by an enemy pawn, as a share of the most that kind of piece can
  reach), if it is passive (PASSIVE or less). This one is read from the board, not the engine;
  the engine's line is shown with it: whether, and with which move, its plan improves that piece
  in your next four moves. (A bad bishop stuck behind its own pawns often has no such move.)

Then each is compared with what you thought: squares you circled or named, arrows onto the
square, the piece you called worst, or a candidate move that moves that piece.
"""

from __future__ import annotations

import re

import chess

from .critical import line_moves

PV_PLIES = 8
PV_NODES = 100_000
EXTEND_NODES = 50_000
CREATED_MIN_LOSS = 0.03  # a weakness your move creates is flagged when the move cost at least this
DECIDED = 0.20
SLOW = 0.30  # the worst piece is a slow, positional question: only asked while the game is within 30-70%  # no weaknesses once your win chance is below this (or above 1 - this)
LOOKBACK = 10  # pointing at a square counts for a weakness that shows up within this many moves
PASSIVE = 0.25  # a worst piece reaches at most this share of the squares that kind of piece can
MAX_REACH = {chess.KNIGHT: 8, chess.BISHOP: 13, chess.ROOK: 14, chess.QUEEN: 27}
NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook", chess.QUEEN: "queen",
        chess.KING: "king"}


# ---------------------------------------------------------------- weaknesses


def structural(board: chess.Board, color: chess.Color) -> dict[int, str]:
    """{square: what kind of weakness} for `color`'s pawns and holes."""
    own = board.pieces(chess.PAWN, color)
    enemy = board.pieces(chess.PAWN, not color)
    forward = 1 if color == chess.WHITE else -1
    files = [chess.square_file(s) for s in own]
    out: dict[int, str] = {}
    for sq in own:
        f, r = chess.square_file(sq), chess.square_rank(sq)
        neighbours = [s for s in own if abs(chess.square_file(s) - f) == 1]
        if not neighbours:
            out[sq] = "isolated pawn"
        elif files.count(f) > 1:
            out[sq] = "doubled pawn"
        else:
            # Backward: no neighbour level with it or behind to support it, and an enemy pawn
            # guards the square in front, so it can't advance either.
            behind = [s for s in neighbours if (chess.square_rank(s) - r) * forward <= 0]
            stop = r + forward
            if not behind and 0 <= stop + forward <= 7 and any(
                chess.square_file(e) in (f - 1, f + 1) and chess.square_rank(e) == stop + forward for e in enemy
            ):
                out[sq] = "backward pawn"
    # Holes: squares in your half (and one rank past it) that your pawns can never attack again.
    ranks = range(2, 5) if color == chess.WHITE else range(3, 6)
    for f in range(1, 7):  # holes on the a- and h-files rarely matter
        for r in ranks:
            sq = chess.square(f, r)
            if sq in out or board.piece_at(sq) and board.piece_at(sq).color == color and board.piece_at(sq).piece_type == chess.PAWN:
                continue
            guards = [s for s in own if abs(chess.square_file(s) - f) == 1 and (r - chess.square_rank(s)) * forward > 0]
            if not guards:
                out[sq] = "hole"
    return out


VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def _balance(b: chess.Board, color: chess.Color) -> int:
    """Material of `color` minus the other side's (pawn = 1)."""
    return sum(VALUE[p.piece_type] * (1 if p.color == color else -1) for p in b.piece_map().values())


def _landings(board: chess.Board, moves: list[chess.Move], last_capture: int | None = None
              ) -> dict[chess.Color, dict[str, set[int]]]:
    """Per side, what it does in a line:
      'piece'   squares its pieces (not pawns) settle on: not by capturing a piece, and not taken
                straight back;
      'capture' squares where it takes a pawn, other than as a recapture;
      'wins'    squares (as they stand at the start of the line) of pieces it wins: taken, not as
                a recapture, while nothing defended it, not taken back on the next move, and the
                side isn't behind on material by the end of the line (giving a bishop to win
                the loose one back is still winning it).
    A recapture is a capture on the square the other side has just captured on (`last_capture`
    for the line's first move: where the move before the position captured), i.e. an exchange."""
    out = {c: {"piece": set(), "capture": set(), "wins": set()} for c in (chess.WHITE, chess.BLACK)}
    b = board.copy(stack=False)
    legal = []
    for m in moves:
        if m not in b.legal_moves:
            break
        legal.append(m)
        b.push(m)
    b = board.copy(stack=False)
    origin = {sq: sq for sq in chess.SQUARES}  # where the piece now on a square started the line
    captured_prev = False
    won = []
    for i, m in enumerate(legal):
        side = b.turn
        victim = b.piece_at(m.to_square)
        taken_back = i + 1 < len(legal) and legal[i + 1].to_square == m.to_square
        before = last_capture if i == 0 else (legal[i - 1].to_square if captured_prev else None)
        captured_prev = victim is not None
        if victim is not None and m.to_square != before:
            if victim.piece_type == chess.PAWN:
                out[side]["capture"].add(m.to_square)
            elif (i + 1 < len(legal) and not taken_back and origin.get(m.to_square) == m.to_square
                  and not b.is_attacked_by(victim.color, m.to_square)):  # nothing defended it
                # (a capture on the line's last move isn't "won": the reply isn't known)
                won.append((side, m.to_square, VALUE[victim.piece_type]))
        if b.piece_type_at(m.from_square) != chess.PAWN and not taken_back and (
                victim is None or victim.piece_type == chess.PAWN):
            out[side]["piece"].add(m.to_square)
        origin[m.to_square] = origin.get(m.from_square)
        origin[m.from_square] = None
        b.push(m)
    for side, sq, value in won:
        if _balance(b, side) - _balance(board, side) >= 0:
            out[side]["wins"].add(sq)
    return out


def weaknesses(board: chess.Board, me: chess.Color, best_pv: list[chess.Move], threat: dict | None,
               last_capture: int | None = None) -> list[dict]:
    """Weaknesses (yours and theirs) that the engine's lines use. `last_capture`: the square the
    move before this position captured on, if it was a capture (taking back there is an exchange)."""
    lands = _landings(board, best_pv[:PV_PLIES], last_capture)
    if threat and threat.get("line_san"):
        passed = board.copy(stack=False)
        passed.push(chess.Move.null())
        theirs_if_passed = _landings(passed, line_moves(passed, threat["line_san"]))[not me]
        for k in ("piece", "capture"):  # pieces it would win are threats (question 3), not weaknesses
            lands[not me][k] |= theirs_if_passed[k]
    out = []
    for side in (me, not me):
        whose = "yours" if side == me else "theirs"
        for sq, kind in structural(board, side).items():
            used = lands[not side]["piece"] if kind == "hole" else lands[not side]["capture"]
            if sq in used:
                out.append({"square": chess.square_name(sq), "kind": kind, "side": whose})
        for sq in lands[not side]["wins"]:
            piece = board.piece_at(sq)
            if piece and piece.color == side and piece.piece_type not in (chess.PAWN, chess.KING):
                out.append({"square": chess.square_name(sq), "kind": f"loose {NAME[piece.piece_type]}", "side": whose})
    out.sort(key=lambda w: (w["side"] != "theirs", "hole" in w["kind"], w["square"]))
    return out


# ---------------------------------------------------------------- worst piece


def activity(board: chess.Board, square: int) -> float:
    """How free a piece is: squares it reaches that aren't your own pieces and aren't covered by an
    enemy pawn, as a share of the most that kind of piece can ever have."""
    piece = board.piece_at(square)
    enemy_pawn_cover = chess.SquareSet()
    for p in board.pieces(chess.PAWN, not piece.color):
        enemy_pawn_cover |= board.attacks(p)
    reach = board.attacks(square) & ~board.occupied_co[piece.color] & ~enemy_pawn_cover
    return len(reach) / MAX_REACH[piece.piece_type]


def worst_piece(board: chess.Board, me: chess.Color, pv: list[chess.Move]) -> dict | None:
    """Your least active piece, if it is passive enough; with the engine's move for it, if its line has one."""
    home = {chess.WHITE: {chess.A1, chess.H1, chess.D1}, chess.BLACK: {chess.A8, chess.H8, chess.D8}}[me]
    early = board.fullmove_number < 15  # an undeveloped rook or queen isn't "worst" yet
    pieces = sorted(
        ({"square": chess.square_name(sq), "piece": NAME[p.piece_type], "activity": round(activity(board, sq), 2)}
         for sq, p in board.piece_map().items() if p.color == me and p.piece_type in MAX_REACH
         and not (early and sq in home and p.piece_type in (chess.ROOK, chess.QUEEN))),
        key=lambda p: p["activity"],
    )
    if not pieces or pieces[0]["activity"] > PASSIVE:
        return None
    worst = {**pieces[0], "engine_move": None, "engine_move_no": None, "engine_line": None}
    sq = chess.parse_square(worst["square"])
    b = board.copy(stack=False)
    for i, m in enumerate(pv[:PV_PLIES]):
        if m not in b.legal_moves:
            break
        if b.turn == me and m.from_square == sq:  # the engine's plan improves it
            worst["engine_move"] = b.san(m)
            worst["engine_move_no"] = i // 2 + 1  # your 1st, 2nd... move in the engine's line
            worst["engine_line"] = board.variation_san(pv[: i + 1])
            break
        b.push(m)
    # Only a piece the engine actually brings to life is worth raising: one that stays bad in the
    # best line too is just how the position is.
    return worst if worst["engine_move"] else None


def follow(worst: dict, records: dict, me: chess.Color) -> dict:
    """What became of a worst piece in the game: followed through your moves from where it was
    worst until it stopped being passive ("active", with the move that last moved it), left the
    board ("gone"), or the game ended with it still passive ("stuck")."""
    sq = chess.parse_square(worst["square"])
    last_move, last_no = None, worst["move"]
    for ply in sorted(records):
        r = records[ply]
        if r["move_number"] < worst["move"] or not r.get("fen"):
            continue
        b = chess.Board(r["fen"])
        piece = b.piece_at(sq)
        if piece is None or piece.color != me or NAME.get(piece.piece_type) != worst["piece"]:
            return {"kind": "gone", "move": r["move_number"]}
        a = activity(b, sq)
        if r["move_number"] > worst["move"] and a > PASSIVE:
            return {"kind": "active", "move": r["move_number"], "activity": round(a, 2), "by": last_move}
        last_no = r["move_number"]
        if not r.get("move_uci"):
            continue
        m = chess.Move.from_uci(r["move_uci"])
        if m.from_square == sq:
            n = r["move_number"]
            last_move = f"{n}. {b.san(m)}" if me == chess.WHITE else f"{n}... {b.san(m)}"
            sq = m.to_square
        elif b.is_castling(m) and chess.square_rank(sq) == chess.square_rank(m.from_square) and (
                (b.is_kingside_castling(m) and chess.square_file(sq) == 7)
                or (b.is_queenside_castling(m) and chess.square_file(sq) == 0)):
            n = r["move_number"]
            last_move = f"{n}. {b.san(m)}" if me == chess.WHITE else f"{n}... {b.san(m)}"
            sq = chess.square(5 if b.is_kingside_castling(m) else 3, chess.square_rank(sq))
    return {"kind": "stuck", "move": last_no}


def main_line(engine, board: chess.Board) -> list[chess.Move]:
    """The engine's best line, at least PV_PLIES long: Stockfish often reports a short PV, so it is
    continued with further short searches from where it stops."""
    pv = list(engine.analyse(board, nodes=PV_NODES)[0].get("pv", []))
    b = board.copy(stack=False)
    for m in pv:
        b.push(m)
    while len(pv) < PV_PLIES and not b.is_game_over():
        more = engine.analyse(b, nodes=EXTEND_NODES)[0].get("pv", [])
        if not more:
            break
        for m in more[: PV_PLIES - len(pv)]:
            pv.append(m)
            b.push(m)
    return pv


# ---------------------------------------------------------------- compared with your thoughts


def _user_squares(move: dict) -> dict[str, set[str]]:
    """Squares you pointed at in this move's window: 'marked' (circled or named), 'my arrow' (a
    move of yours going there) or 'their arrow' (a move of theirs you drew going there)."""
    out: dict[str, set[str]] = {}
    for d in move.get("drawn_candidates", []):
        uci = d.get("move_uci") or ""
        if len(uci) >= 4:
            out.setdefault(uci[2:4], set()).add("my arrow")
    for san in move.get("drawn_opponent_ideas", []):
        found = re.findall(r"[a-h][1-8]", san)
        if found:
            out.setdefault(found[-1], set()).add("their arrow")
    for sq in move.get("key_squares", []):
        out.setdefault(sq, set()).add("marked")
    return out


def _credit(side: str, hows: set[str]) -> str | None:
    """Whether pointing at a weakness this way means you saw it: a mark always does; for their
    weakness so does your move going there, for yours their move going there."""
    if "marked" in hows:
        return "marked"
    if side == "theirs" and "my arrow" in hows:
        return "your arrow"
    if side == "yours" and "their arrow" in hows:
        return "their arrow"
    return None


def created(record: dict, me: chess.Color) -> list[dict]:
    """Your weaknesses that your move itself created and the opponent's best reply line uses."""
    if not record.get("move_uci") or not record.get("played_line_san"):
        return []
    if (record.get("wp_loss") or 0) < CREATED_MIN_LOSS:
        return []
    before = chess.Board(record["fen"])
    move = chess.Move.from_uci(record["move_uci"])
    if move not in before.legal_moves:
        return []
    after = before.copy(stack=False)
    after.push(move)
    line = []
    b = before.copy(stack=False)
    for tok in re.sub(r"\d+\.(\.\.)?", " ", record["played_line_san"]).split():
        try:
            line.append(b.parse_san(tok))
        except ValueError:
            break
        b.push(line[-1])
    line = line[1:]  # the line starts with your move
    last_capture = move.to_square if before.is_capture(move) else None
    old = structural(before, me)
    out = []
    for w in weaknesses(after, not me, line, None, last_capture):
        if w["side"] != "theirs":  # seen from the opponent's side: "theirs" is yours
            continue
        sq = chess.parse_square(w["square"])
        if w["kind"].startswith("loose"):
            piece = before.piece_at(sq)
            was_loose = piece is not None and piece.color == me and not before.is_attacked_by(me, sq)
            if was_loose:
                continue
        elif old.get(sq) == w["kind"]:
            continue
        out.append({**w, "side": "yours", "created_by": before.san(move), "fen_after": after.fen()})
    return out


def unstoppable(board: chess.Board, pv: list[chess.Move], threat_uci: str) -> bool:
    """Their threat is a capture that still happens in your best line, within your next two moves
    (a trapped piece): whatever you do, they take it."""
    b = chess.Board(board.fen())
    b.push(chess.Move.null())
    threat = chess.Move.from_uci(threat_uci)
    if not b.is_capture(threat):
        return False
    target = threat.to_square
    for i, m in enumerate(pv[:4]):
        if i % 2 == 1 and m.to_square == target:
            return True
    return False


def refresh_threats(report: dict) -> None:
    """After threats are marked unstoppable: their costly moves go to the next cause, and the
    summary's threat counts and win % by cause are counted again."""
    t = report["thresholds"]
    tt = t.get("time_trouble", 30)
    moves = [m for m in report["moves"] if not m.get("skipped")]
    for m in moves:
        if m.get("gap") == "opponent_idea" and (m.get("q3") or {}).get("status") == "unstoppable":
            q4 = m.get("q4") or {}
            if q4.get("wrongly_rejected"):
                m["gap"] = "wrongly_rejected"
            elif not (m.get("drawn_candidates") or m.get("drawn_opponent_ideas") or m.get("key_squares")):
                m["gap"] = "no_thoughts"
            elif not q4.get("found"):
                m["gap"] = "missed_candidate"
            else:
                m["gap"] = "other"
    s = report["summary"]
    if "by_gap" in s:
        by_gap = {g: {"count": 0, "wp_lost": 0.0} for g in s["by_gap"]}
        for m in moves:
            if m.get("gap"):
                by_gap[m["gap"]]["count"] += 1
                by_gap[m["gap"]]["wp_lost"] = round(by_gap[m["gap"]]["wp_lost"] + m["wp_loss"], 3)
        s["by_gap"] = by_gap
    in_tt = lambda m: m.get("clock_before") is not None and m["clock_before"] < tt  # noqa: E731
    status = lambda m: (m.get("q3") or {}).get("status")  # noqa: E731
    real = [m for m in moves if status(m) in ("spotted", "handled", "not_drawn") and not in_tt(m)]
    s["q3_real_threats"] = len(real)
    s["q3_spotted"] = sum(status(m) == "spotted" for m in real)
    s["q3_handled"] = sum(status(m) == "handled" for m in real)
    s["q3_unstoppable"] = sum(status(m) == "unstoppable" for m in moves if not in_tt(m))


def check_move(engine, move: dict, record: dict, me: chess.Color) -> tuple[dict, dict]:
    """(q1, q2) for one of your moves."""
    board = chess.Board(record["fen"])
    pv = main_line(engine, board)

    found = _user_squares(move)
    wp = record.get("wp_before")
    decided = wp is not None and not DECIDED <= wp <= 1 - DECIDED
    last = record.get("opponent_move_san") or ""
    last_capture = chess.parse_square(re.findall(r"[a-h][1-8]", last)[-1]) if "x" in last else None
    weak = [] if decided else weaknesses(board, me, pv, record.get("threat"), last_capture)
    if not decided:
        have = {(w["square"], w["kind"]) for w in weak}
        weak += [w for w in created(record, me) if (w["square"], w["kind"]) not in have]
    for w in weak:
        w["you"] = _credit(w["side"], found.get(w["square"], set()))
    q1 = {"weaknesses": weak, "found": [w["square"] for w in weak if w["you"]]}

    q3 = move.get("q3") or {}
    threat = record.get("threat") or {}
    if q3.get("status") in ("not_drawn", "handled") and threat.get("move_uci") and unstoppable(board, pv, threat["move_uci"]):
        q3["status"] = "unstoppable"  # they take it even after your best move: not a threat you could stop

    worst = worst_piece(board, me, pv) if wp is None or SLOW <= wp <= 1 - SLOW else None
    if worst:
        said = " ".join(move.get("worst_pieces") or []).lower()
        moved = any((d.get("move_uci") or "").startswith(worst["square"]) for d in move.get("drawn_candidates", []))
        worst["you"] = ("named" if worst["square"] in said or worst["piece"] in said
                        else "moved it in a candidate" if moved else None)
    return q1, {"worst": worst}


def annotate(report: dict, analysis: dict, engine, me: chess.Color) -> None:
    """Add q1 / q2 to each reviewed move, and per-game lists to the summary. Each weakness and each
    worst piece counts once per game (found if you pointed at it while it was there); time-trouble
    moves are checked but, like threats, not counted."""
    records = {r["ply"]: r for r in analysis["moves"]}
    tt = report["thresholds"].get("time_trouble", 30)
    weak_seen: dict[tuple, dict] = {}
    worst_seen: dict[tuple, dict] = {}
    pointed: dict[str, list[tuple[int, set[str]]]] = {}  # square -> [(move number, how)]
    for m in report["moves"]:
        if not m.get("skipped"):
            for sq, hows in _user_squares(m).items():
                pointed.setdefault(sq, []).append((m["move_number"], hows))
    for m in report["moves"]:
        if m.get("skipped"):
            continue
        q1, q2 = check_move(engine, m, records[m["ply"]], me)
        m["q1"], m["q2"] = q1, q2
        if (m.get("clock_before") if m.get("clock_before") is not None else tt) < tt:
            continue
        for w in q1["weaknesses"]:
            entry = weak_seen.setdefault((w["side"], w["square"]), {**w, "move": m["move_number"], "you": None})
            entry["last"] = m["move_number"]
    for entry in weak_seen.values():
        # Found if you pointed at the square at any time before it stopped being a weakness
        # ("weak e6" at move 18 counts for the e6 the engine went for at move 22).
        for move_no, hows in pointed.get(entry["square"], []):
            how = _credit(entry["side"], hows)
            # One your own move creates is seen only if you pointed at it before playing that move.
            first = entry["move"] if entry.get("created_by") else entry["move"] - LOOKBACK
            last = entry["move"] if entry.get("created_by") else entry["last"]
            if how and first <= move_no <= last:
                entry["you"], entry["you_move"] = how, move_no
                break
    for m in report["moves"]:
        if m.get("skipped") or "q2" not in m:
            continue
        if (m.get("clock_before") if m.get("clock_before") is not None else tt) < tt:
            continue
        w = m["q2"]["worst"]
        if w:
            entry = worst_seen.setdefault((w["piece"], w["square"]), {**w, "move": m["move_number"], "you": None})
            entry["you"] = entry["you"] or w["you"]
    refresh_threats(report)
    s = report["summary"]
    s["q1_list"] = list(weak_seen.values())
    s["q1_weaknesses"], s["q1_found"] = len(weak_seen), sum(bool(w["you"]) for w in weak_seen.values())
    for w in worst_seen.values():
        w["fate"] = follow(w, records, me)
    s["q2_list"] = list(worst_seen.values())
    s["q2_positions"], s["q2_found"] = len(worst_seen), sum(bool(w["you"]) for w in worst_seen.values())
