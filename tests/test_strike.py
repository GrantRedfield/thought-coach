import io

import chess
import chess.pgn

from thoughtcoach.strike import find, first_break, made_by


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
    assert s["text"] == "None: the game was decided by your 3... Nf6 (your win chance 45% → 0%), cashed in with 4. Qxf7#."


def test_an_even_trade_is_not_a_strike():
    g = game('[Result "1/2-1/2"]\n\n1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 1/2-1/2')
    analysis = {"moves": [rec(1, 0.5, 0.5), rec(3, 0.5, 0.5), rec(5, 0.5, 0.52)]}
    s = find(g, analysis, chess.WHITE)
    assert s["side"] is None and "never broke open" in s["text"]


def test_the_first_break_and_the_trades_after_it():
    g = game('[Result "*"]\n\n1. Nf3 Nf6 2. g3 g6 3. Bg2 Bg7 4. O-O O-O 5. c4 c5 6. Nc3 Nc6 '
             '7. b3 b6 8. d4 cxd4 9. Nxd4 Nxd4 10. Qxd4 d6 *')
    analysis = {"moves": [rec(15, 0.55, 0.55), rec(17, 0.55, 0.56), rec(19, 0.56, 0.54)]}
    br = first_break(g, analysis, chess.WHITE)
    assert br["move"] == "8. d4" and br["side"] == "you" and br["centre"]
    assert br["line"] == "8... cxd4 9. Nxd4 Nxd4 10. Qxd4"
    assert br["text"] == "You opened the centre with 8. d4; after 8... cxd4 9. Nxd4 Nxd4 10. Qxd4 the balance held (55% → 54%)."


def test_opening_pawn_tension_is_not_a_break():
    g = game('[Result "*"]\n\n1. d4 d5 2. c4 e6 3. Nc3 Nf6 *')
    assert first_break(g, {"moves": []}, chess.WHITE) is None


def test_a_threat_counts_only_when_the_move_makes_it():
    b = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 4")
    a3, nc3 = b.parse_san("a3"), b.parse_san("Nc3")
    after = b.copy(); after.push(a3)
    assert not made_by(after, a3, "Nxe5")  # the knight on f3 hit e5 before a3
    after = b.copy(); after.push(nc3)
    assert made_by(after, nc3, "Nd5")  # the knight that moved
    # a discovered attack: the bishop on b2 sees h8 once the knight on e5 moves off the diagonal
    d = chess.Board("7k/8/8/4N3/8/8/1B6/K7 w - - 0 1")
    ng4 = d.parse_san("Ng4")
    after = d.copy(); after.push(ng4)
    assert made_by(after, ng4, "Bh8")
