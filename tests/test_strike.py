import io

import chess
import chess.pgn

from thoughtcoach.strike import find


def game(pgn: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(pgn))


def rec(ply, before, after):
    return {"ply": ply, "wp_before": before, "wp_after": after}


MATE = '[Result "1-0"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0'


def test_they_strike_after_your_move_opens_the_door():
    # 3...Nf6 costs 10% but the game is still balanced: 4. Qxf7# is their strike.
    analysis = {"moves": [rec(2, 0.5, 0.5), rec(4, 0.5, 0.48), rec(6, 0.45, 0.35)]}
    s = find(game(MATE), analysis, chess.BLACK)
    assert s["side"] == "them" and s["move"] == "4. Qxf7#" and s["outcome"] == "worked"
    assert s["door"]["move"] == "3... Nf6" and s["door"]["side"] == "you"
    assert s["text"].startswith("They struck first with 4. Qxf7#, after your 3... Nf6 opened the door")


def test_cashing_in_a_blunder_is_not_a_strike():
    # 3...Nf6 already drops you to 0%: 4. Qxf7# only cashes it in.
    analysis = {"moves": [rec(2, 0.5, 0.5), rec(4, 0.5, 0.48), rec(6, 0.45, 0.0)]}
    s = find(game(MATE), analysis, chess.BLACK)
    assert s["side"] is None and s["decided_by"] == "3... Nf6"
    assert s["text"] == "None: the game was decided by your 3... Nf6 (-45%), cashed in with 4. Qxf7#."


def test_an_even_trade_is_not_a_strike():
    g = game('[Result "1/2-1/2"]\n\n1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 1/2-1/2')
    analysis = {"moves": [rec(1, 0.5, 0.5), rec(3, 0.5, 0.5), rec(5, 0.5, 0.52)]}
    s = find(g, analysis, chess.WHITE)
    assert s["side"] is None and "never broke open" in s["text"]
