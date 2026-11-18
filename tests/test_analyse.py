from pathlib import Path

import chess
import chess.engine
import chess.pgn
import pytest

from thoughtcoach.analyse import (
    Rules,
    analyse_pgn_game,
    game_id,
    is_decided,
    parse_time_control,
    player_color,
)
from thoughtcoach.engine import Engine, EngineSettings, find_stockfish, win_prob
from thoughtcoach.report import fmt_clock, render

PGN = Path(__file__).parent / "data" / "blackburne.pgn"


def load_game() -> chess.pgn.Game:
    with open(PGN, encoding="utf-8") as f:
        return chess.pgn.read_game(f)


# ------------------------------------------------------------- no engine needed


def test_parse_time_control():
    assert parse_time_control("180+2") == (180.0, 2.0)
    assert parse_time_control("600") == (600.0, 0.0)
    assert parse_time_control("-") == (None, 0.0)
    assert parse_time_control(None) == (None, 0.0)


def test_game_id_and_color():
    game = load_game()
    assert game_id(game, "x") == "AbCd1234"
    game.headers["Site"] = "https://lichess.org/game/58wYv8H8"  # newer lichess export format
    assert game_id(game, "x") == "58wYv8H8"
    assert player_color(game, "tester", None) == chess.WHITE
    assert player_color(game, "OPPONENT", None) == chess.BLACK
    assert player_color(game, "nobody", None) is None
    assert player_color(game, None, "black") == chess.BLACK


def cp_info(cp: int, pov=chess.WHITE) -> dict:
    return {"score": chess.engine.PovScore(chess.engine.Cp(cp), pov)}


def test_lichess_win_prob():
    assert win_prob(cp_info(0), chess.WHITE) == pytest.approx(0.5)
    # Reference values of lichess's curve: +100cp ~ 59.1%, +260cp ~ 72.3%.
    assert win_prob(cp_info(100), chess.WHITE) == pytest.approx(0.591, abs=0.001)
    assert win_prob(cp_info(260), chess.WHITE) == pytest.approx(0.723, abs=0.001)
    # Point of view: Black's score is the mirror image.
    assert win_prob(cp_info(100), chess.BLACK) == pytest.approx(1 - 0.591, abs=0.001)
    # Capped at +-1000cp; mate counts as the cap.
    assert win_prob(cp_info(5000), chess.WHITE) == win_prob(cp_info(1000), chess.WHITE)
    mate = {"score": chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE)}
    assert win_prob(mate, chess.WHITE) == pytest.approx(0.975, abs=0.001)
    assert win_prob(mate, chess.BLACK) == pytest.approx(0.025, abs=0.001)


def test_sf_wdl_win_prob():
    wdl_info = {"wdl": chess.engine.PovWdl(chess.engine.Wdl(700, 200, 100), chess.WHITE)}
    assert win_prob(wdl_info, chess.WHITE, "sf-wdl") == pytest.approx(0.8)
    assert win_prob(wdl_info, chess.BLACK, "sf-wdl") == pytest.approx(0.2)
    # No WDL from the engine: fall back to the score.
    assert win_prob(cp_info(0), chess.WHITE, "sf-wdl") == pytest.approx(0.5, abs=0.01)
    with pytest.raises(ValueError):
        win_prob(cp_info(0), chess.WHITE, "nonsense")


def test_is_decided():
    assert is_decided(0.99, 0.98, 0.05)  # still winning
    assert is_decided(0.01, 0.00, 0.05)  # still lost
    assert not is_decided(0.99, 0.50, 0.05)  # threw away a win: must be analysed
    assert not is_decided(0.99, 0.01, 0.05)  # both "outside", but opposite sides
    assert not is_decided(0.60, 0.55, 0.05)


def test_fmt_clock():
    assert fmt_clock(65) == "1:05"
    assert fmt_clock(3725) == "1:02:05"
    assert fmt_clock(None).strip() == "-"


# ------------------------------------------------------------- needs Stockfish


def stockfish_path():
    try:
        return find_stockfish()
    except FileNotFoundError:
        return None


needs_stockfish = pytest.mark.skipif(stockfish_path() is None, reason="Stockfish not installed")


@needs_stockfish
def test_full_game_small_budget():
    game = load_game()
    settings = EngineSettings(nodes=50_000, multipv=3, hash_mb=16)
    rules = Rules(book_moves=1)
    with Engine(stockfish_path(), settings) as engine:
        data = analyse_pgn_game(game, chess.WHITE, engine, rules, "x")

    moves = data["moves"]
    assert [m["move_san"] for m in moves] == ["e4", "Nf3", "Bc4", "Nxe5", "Nxf7", "Rf1", "Be2"]
    assert moves[0]["skipped"] == "book"
    # Time spent = previous clock - clock after + increment (180+2).
    assert moves[3]["time_spent"] == pytest.approx(177 - 160 + 2)
    assert moves[3]["opponent_move_san"] == "Nd4"

    for m in moves:
        if m["skipped"]:
            continue
        assert 0.0 <= m["wp_after"] <= 1.0
        assert m["wp_loss"] >= 0.0
        assert m["candidates"], "best move must always be a candidate"
        assert m["candidates"][0]["move_san"] == m["best_move_san"]

    # 7. Be2 is played in check: no null-move threat is possible.
    be2 = moves[-1]
    assert be2["skipped"] in ("decided", "forced") or be2["threat_skipped"] == "in check"

    # 4. Nxe5?? walks into ...Qg5: the engine should see a big loss there.
    assert moves[3]["skipped"] is None
    assert moves[3]["wp_loss"] > 0.1

    assert "MOVE BY MOVE" in render(data)


def test_checkmate_on_the_board_is_100_percent():
    import chess.engine
    from thoughtcoach.engine import win_prob
    mated = {"score": chess.engine.PovScore(chess.engine.Mate(0), chess.WHITE)}  # white to move, mated
    assert win_prob(mated, chess.BLACK) == 1.0 and win_prob(mated, chess.WHITE) == 0.0
    mate_in_2 = {"score": chess.engine.PovScore(chess.engine.Mate(2), chess.BLACK)}
    assert 0.97 < win_prob(mate_in_2, chess.BLACK) < 1.0  # lichess curve caps mate-in-N
