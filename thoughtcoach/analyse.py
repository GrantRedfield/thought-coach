"""Per-move engine analysis of one player's moves in a PGN game.

For each of my moves we look at the position I faced (after the opponent's move) and record:
  - win probability (WP) before my move = WP of the engine's best line
  - WP after my move, from a search restricted to the move I played
  - the multi-PV candidates within `candidate_margin` WP of the best
  - the opponent's idea: the engine's best move for the opponent if I "pass" (null move)
All win probabilities are from my point of view.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import chess
import chess.pgn
import chess.polyglot

from .engine import Engine, win_prob
from .progress import report as report_progress

PV_LENGTH = 8  # plies of each line kept in the output


@dataclass
class Rules:
    book_moves: int = 8  # skip my moves while the full-move number is <= this
    book_path: str | None = None  # optional Polyglot .bin; my move found in it => book
    decided: float = 0.05  # skip if WP before my move < decided or > 1 - decided
    candidate_margin: float = 0.05  # candidates = lines within this WP of the best
    wp_model: str = "lichess"  # "lichess" (human-fitted curve) or "sf-wdl" (Stockfish WDL)


# ---------------------------------------------------------------- PGN helpers


def parse_time_control(tc: str | None) -> tuple[float | None, float]:
    """'600+5' -> (600.0, 5.0). Anything else ('-', missing, '1/259200') -> (None, 0.0)."""
    match = re.fullmatch(r"(\d+)(?:\+(\d+))?", (tc or "").strip())
    if not match:
        return None, 0.0
    return float(match.group(1)), float(match.group(2) or 0)


def game_id(game: chess.pgn.Game, fallback: str) -> str:
    """Lichess game id from the Site header, else `fallback`."""
    match = re.search(r"lichess\.org/(?:game/)?(\w{8})", game.headers.get("Site", ""))
    return match.group(1) if match else fallback


def player_color(game: chess.pgn.Game, me: str | None, color: str | None) -> chess.Color | None:
    """Which side I played: explicit --color wins, else match --me against the player headers."""
    if color:
        return chess.WHITE if color.lower().startswith("w") else chess.BLACK
    if me:
        for side, header in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
            if game.headers.get(header, "").lower() == me.lower():
                return side
    return None


def _round(x: float | None, digits: int = 3) -> float | None:
    return None if x is None else round(x, digits)


# ---------------------------------------------------------------- analysis


def is_book(board: chess.Board, move: chess.Move, rules: Rules, book) -> bool:
    if board.fullmove_number <= rules.book_moves:
        return True
    if book is not None:
        return any(entry.move == move for entry in book.find_all(board))
    return False


def is_decided(best_wp: float, my_wp: float, threshold: float) -> bool:
    """Decided = the best line AND my move both stay on the same side of the threshold.

    Checking only the position before my move would hide moves that throw away a won game.
    """
    both_won = best_wp > 1 - threshold and my_wp > 1 - threshold
    both_lost = best_wp < threshold and my_wp < threshold
    return both_won or both_lost


def analyse_position(
    board: chess.Board,
    move: chess.Move,
    me: chess.Color,
    engine: Engine,
    rules: Rules,
    book=None,
) -> dict:
    """Engine facts about one position where it is my move. `board` is not modified."""
    rec: dict = {"skipped": None}

    if is_book(board, move, rules, book):
        rec["skipped"] = "book"
        return rec
    if board.legal_moves.count() == 1:
        rec["skipped"] = "forced"
        return rec

    # 1. Multi-PV search: WP before the move and the candidate set.
    infos = [i for i in engine.analyse(board, multipv=engine.settings.multipv) if i.get("pv")]
    lines = [(info["pv"], win_prob(info, me, rules.wp_model)) for info in infos]
    best_wp = lines[0][1]
    rec["wp_before"] = _round(best_wp)

    # 2. The move I played. If it is one of the MultiPV lines, take its WP from that same search
    #    (consistent with the other candidates); otherwise search it on its own with root_moves.
    in_multipv = [(pv, wp) for pv, wp in lines if pv[0] == move]
    if in_multipv:
        played_pv, my_wp = in_multipv[0]
    else:
        played = engine.analyse(board, multipv=1, root_moves=[move])[0]
        played_pv, my_wp = played["pv"], win_prob(played, me, rules.wp_model)

    if is_decided(best_wp, my_wp, rules.decided):
        rec.update(skipped="decided", wp_after=_round(my_wp), wp_loss=_round(max(0.0, best_wp - my_wp)))
        return rec

    candidates = [
        {
            "move_san": board.san(pv[0]),
            "move_uci": pv[0].uci(),
            "wp": _round(wp),
            "line_san": board.variation_san(pv[:PV_LENGTH]),
        }
        for pv, wp in lines
        if best_wp - wp <= rules.candidate_margin
    ]
    rec.update(
        {
            "wp_after": _round(my_wp),
            "wp_loss": _round(max(0.0, best_wp - my_wp)),
            "best_move_san": board.san(lines[0][0][0]),
            "played_line_san": board.variation_san(played_pv[:PV_LENGTH]),
            "played_wp_source": "multipv" if in_multipv else "own search",
            "played_within_margin": best_wp - my_wp <= rules.candidate_margin,
            "played_in_candidates": any(c["move_uci"] == move.uci() for c in candidates),
            "candidates": candidates,
            # Every MultiPV line made the cut, so there may be more good moves than we listed.
            "candidates_may_be_truncated": len(candidates) == engine.settings.multipv,
        }
    )

    # 3. Opponent's idea: let the opponent move twice (null move). Impossible when I'm in check.
    rec["threat"] = None
    if board.is_check():
        rec["threat_skipped"] = "in check"
    else:
        passed = board.copy(stack=False)
        passed.push(chess.Move.null())
        passed = chess.Board(passed.fen())  # fresh board: no "0000" in the move list sent to the engine
        info = engine.analyse(passed, multipv=1)[0]
        if info.get("pv"):
            null_wp = win_prob(info, me, rules.wp_model)
            rec["threat"] = {
                "move_san": passed.san(info["pv"][0]),
                "move_uci": info["pv"][0].uci(),
                "wp_if_ignored": _round(null_wp),
                "swing": _round(best_wp - null_wp),
                "line_san": passed.variation_san(info["pv"][:PV_LENGTH]),
            }
    return rec


def opponent_clocks(game: chess.pgn.Game, me: chess.Color) -> dict[int, float | None]:
    """{ply of each of my moves: the opponent's time left while I think} (for analyses saved
    before 'opp_clock' was recorded)."""
    base, _ = parse_time_control(game.headers.get("TimeControl"))
    clock = {chess.WHITE: base, chess.BLACK: base}
    board, out = game.board(), {}
    for node in game.mainline():
        mover = board.turn
        if mover == me:
            out[board.ply() + 1] = clock[not me]
        if node.clock() is not None:
            clock[mover] = node.clock()
        board.push(node.move)
    return out


def live_move(rec: dict) -> dict:
    """One analysed move, as the live page shows it."""
    return {"ply": rec["ply"], "n": rec["move_number"], "san": rec["move_san"], "wp": rec.get("wp_after"),
            "loss": rec.get("wp_loss"), "spent": rec.get("time_spent")}


def analyse_game(
    game: chess.pgn.Game,
    me: chess.Color,
    engine: Engine,
    rules: Rules,
    book=None,
    progress=None,
) -> list[dict]:
    """Walk the main line and analyse every move played by `me`."""
    base, increment = parse_time_control(game.headers.get("TimeControl"))
    clock = {chess.WHITE: base, chess.BLACK: base}  # remaining time before each side's next move
    board = game.board()
    records = []
    mainline = list(game.mainline())
    opponent_move_san = None

    for index, node in enumerate(mainline):
        move, mover = node.move, board.turn
        clock_after = node.clock()  # lichess %clk: time left after the move, increment included

        if mover == me:
            clock_before = clock[me]
            spent = None
            if clock_after is not None and clock_before is not None:
                spent = max(0.0, clock_before - clock_after + increment)
            rec = {
                "ply": board.ply() + 1,
                "move_number": board.fullmove_number,
                "fen": board.fen(),
                "opponent_move_san": opponent_move_san,
                "move_san": board.san(move),
                "move_uci": move.uci(),
                "clock_before": clock_before,
                "clock_after": clock_after,
                "opp_clock": clock[not me],  # their time left while you think
                "time_spent": _round(spent, 1),
            }
            rec.update(analyse_position(board, move, me, engine, rules, book))
            records.append(rec)
            report_progress("engine", done=index + 1, total=len(mainline), move=live_move(rec))
            if progress:
                progress(index + 1, len(mainline))
        else:
            opponent_move_san = board.san(move)

        if clock_after is not None:
            clock[mover] = clock_after
        board.push(move)

    return records


def analyse_pgn_game(
    game: chess.pgn.Game,
    me: chess.Color,
    engine: Engine,
    rules: Rules,
    fallback_id: str,
    progress=None,
) -> dict:
    """Full JSON-ready result for one game."""
    book = chess.polyglot.open_reader(rules.book_path) if rules.book_path else None
    try:
        moves = analyse_game(game, me, engine, rules, book, progress)
    finally:
        if book is not None:
            book.close()

    h = game.headers
    return {
        "game": {
            "id": game_id(game, fallback_id),
            "site": h.get("Site"),
            "date": h.get("UTCDate") or h.get("Date"),
            "white": h.get("White"),
            "black": h.get("Black"),
            "white_elo": h.get("WhiteElo"),
            "black_elo": h.get("BlackElo"),
            "result": h.get("Result"),
            "time_control": h.get("TimeControl"),
            "opening": h.get("Opening"),
            "my_color": "white" if me == chess.WHITE else "black",
        },
        "settings": {"engine": engine.name, **asdict(engine.settings), **asdict(rules)},
        "moves": moves,
    }
