"""Lichess study chapters linked from story cards."""

import chess

from thoughtcoach import studies


def test_ending_kinds():
    assert studies.ending(chess.Board("8/5k2/8/4P3/8/8/5K2/8 w - - 0 1")) == "pawn"
    assert studies.ending(chess.Board("8/5k2/8/r3P3/8/8/5K2/4R3 w - - 0 1")) == "rook"
    assert studies.ending(chess.Board("8/5k2/3n4/4P3/8/2B5/5K2/8 w - - 0 1")) == "minor"
    assert studies.ending(chess.Board("8/5k2/3n4/r3P3/8/2B5/5K2/4R3 w - - 0 1")) == "rook+minor"
    assert studies.ending(chess.Board()) is None


def test_pawn_likeness_counts_the_mirror_for_the_other_colour():
    a = chess.Board("4k3/pp6/8/8/8/8/6PP/4K3 w - - 0 1")
    b = a.mirror()  # same structure, colours swapped
    assert studies._likeness(studies.pawns(a), studies.pawns(a)) == 1.0
    assert studies._likeness(studies.pawns(a), studies.pawns(b)) == 1.0


def test_rook_ending_gets_an_endgame_course_chapter():
    ch = studies.chapter_for("turning", chess.Board("8/5k2/8/r3P3/8/8/5K2/4R3 w - - 0 1"))
    assert ch and ch["about"] == "rook endings" and ch["url"].startswith("https://lichess.org/study/")


def test_stuck_bishop_gets_the_bishop_course():
    board = chess.Board("r1bqk2r/pp1nbppp/2p1pn2/3p4/2PP4/2N1PN2/PPQ2PPP/R1B1KB1R w KQkq - 0 7")
    ch = studies.chapter_for("worst", board, piece=chess.BISHOP)
    assert ch and ch["study"].startswith("Bishops")


def test_no_chapter_for_a_middlegame_turning_point():
    assert studies.chapter_for("turning", chess.Board()) is None


def test_attach_caps_links_and_never_repeats_a_chapter():
    board = chess.Board("8/5k2/8/r3P3/8/8/5K2/4R3 w - - 0 1")
    beats = [{"kind": "turning", "ply": p, "fen": board.fen(), "fill": {}} for p in range(1, 7)]
    studies.attach(beats, lambda b: chess.Board(b["fen"]))
    links = [b["study"]["url"] for b in beats if b.get("study")]
    assert len(links) == studies.MAX_LINKS and len(set(links)) == len(links)
    assert set(beats[0]["study"]) == {"url", "study", "chapter", "by", "about"}  # links and credit only
