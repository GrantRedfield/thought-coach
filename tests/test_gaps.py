"""Gap analysis on hand-made Milestone 1 records and Milestone 3 windows."""

from thoughtcoach.gaps import Thresholds, build, compare_move, render

T = Thresholds(mistake=0.10, threat_swing=0.10, margin=0.05)
FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def record(move_uci="f1c4", move_san="Bc4", wp_after=0.55, threat=None, **extra):
    rec = {
        "ply": 5,
        "move_number": 3,
        "fen": FEN,
        "move_san": move_san,
        "move_uci": move_uci,
        "wp_before": 0.56,
        "wp_after": wp_after,
        "wp_loss": round(max(0.0, 0.56 - wp_after), 3),
        "best_move_san": "Bb5",
        "skipped": None,
        "candidates": [
            {"move_san": "Bb5", "move_uci": "f1b5", "wp": 0.56},
            {"move_san": "Bc4", "move_uci": "f1c4", "wp": 0.55},
            {"move_san": "d4", "move_uci": "d2d4", "wp": 0.54},
        ],
        "threat": threat,
    }
    rec.update(extra)
    return rec


def shape(kind, frm, to, san=None, legal=True, phase="own_time", drawn_s=2.0):
    return {"kind": kind, "from": frm, "to": to, "move_san": san, "legal": legal if to else None,
            "phase": phase, "drawn_s": drawn_s, "brush": "green", "played": None, "on_board_s": 1.0}


def win(*shapes):
    return {"ply": 5, "shapes": list(shapes), "own_time_s": 10.0, "opponent_time_s": 3.0, "time_spent_pgn": 10.0}


def test_found_missed_and_drawn_duplicates():
    w = win(shape("candidate", "f1", "b5", "Bb5"), shape("candidate", "f1", "b5", "Bb5"),
            shape("candidate", "f1", "c4", "Bc4"))
    m = compare_move(record(), w, T)
    assert m["q4"]["found"] == ["Bb5", "Bc4"]
    assert m["q4"]["missed"] == ["d4"]
    assert m["q4"]["wrongly_rejected"] == []  # Bc4 is within the margin: rejecting Bb5 was fine
    assert [d["times_drawn"] for d in m["drawn_candidates"]] == [2, 1]
    assert not m["mistake"] and m["gap"] is None


def test_wrongly_rejected():
    w = win(shape("candidate", "f1", "b5", "Bb5"), shape("candidate", "d2", "d3", "d3"))
    m = compare_move(record(move_uci="d2d3", move_san="d3", wp_after=0.40), w, T,
                     evaluate=lambda fen, uci: 0.40)
    assert m["mistake"] and m["gap"] == "wrongly_rejected"
    assert m["q4"]["wrongly_rejected"] == ["Bb5"]
    d3 = next(d for d in m["drawn_candidates"] if d["move_san"] == "d3")
    assert d3["played"] and d3["loss"] == 0.16


def test_threat_not_drawn_explains_the_mistake():
    threat = {"move_san": "Nd4", "move_uci": "c6d4", "swing": 0.20, "wp_if_ignored": 0.36}
    w = win(shape("candidate", "d2", "d3", "d3"))
    m = compare_move(record(move_uci="d2d3", move_san="d3", wp_after=0.38, threat=threat), w, T)
    assert m["q3"]["status"] == "not_drawn"
    assert m["gap"] == "opponent_idea"


def test_threat_drawn_is_spotted():
    threat = {"move_san": "Nd4", "move_uci": "c6d4", "swing": 0.20, "wp_if_ignored": 0.36}
    w = win(shape("opponent_idea", "c6", "d4", "Nd4"), shape("candidate", "d2", "d3", "d3"))
    m = compare_move(record(move_uci="d2d3", move_san="d3", wp_after=0.38, threat=threat), w, T)
    assert m["q3"]["status"] == "spotted"
    assert m["gap"] == "missed_candidate"  # saw the threat, but none of the good moves was drawn


def test_nothing_drawn():
    m = compare_move(record(move_uci="d2d3", move_san="d3", wp_after=0.40), None, T)
    assert m["gap"] == "no_thoughts"


def test_build_summary_and_render():
    analysis = {
        "game": {"id": "AbCd1234", "white": "W", "black": "B", "result": "*", "time_control": "180+0",
                 "my_color": "white"},
        "settings": {"candidate_margin": 0.05},
        "moves": [
            {"skipped": "book", "ply": 1},
            record(move_uci="d2d3", move_san="d3", wp_after=0.40),
        ],
    }
    thinking = {"windows": [win(shape("candidate", "f1", "b5", "Bb5"), shape("candidate", "d2", "d3", "d3"))]}
    report = build(analysis, thinking, T)
    s = report["summary"]
    assert s["moves_reviewed"] == 1 and s["skipped"] == {"book": 1}
    assert s["by_gap"]["wrongly_rejected"] == {"count": 1, "wp_lost": 0.16}
    text = render(report)
    assert "good move considered, then rejected: Bb5" in text


def test_threat_handled_by_the_move_played():
    threat = {"move_san": "Nxe4", "move_uci": "c6e4", "wp_if_ignored": 0.30, "swing": 0.26}
    assert compare_move(record(threat=threat), win(), T)["q3"]["status"] == "handled"  # no loss, clear of it
    m = compare_move(record(wp_after=0.31, threat=threat), win(), T)  # my move allowed it
    assert m["q3"]["status"] == "not_drawn" and m["gap"] == "opponent_idea"


def test_drawn_move_within_margin_counts_as_good():
    # Nc3 is not in the engine's list, but it scores 0.53: within 0.05 of the best (0.56).
    w = win(shape("candidate", "b1", "c3", "Nc3"), shape("candidate", "d2", "d3", "d3"))
    m = compare_move(record(move_uci="d2d3", move_san="d3", wp_after=0.40), w, T,
                     evaluate=lambda fen, uci: {"b1c3": 0.53, "d2d3": 0.40}[uci])
    assert "Nc3" in m["q4"]["found"] and m["q4"]["wrongly_rejected"] == ["Nc3"]
    assert m["gap"] == "wrongly_rejected" and m["q4"]["drawn_not_good"] == ["d3"]


def test_threat_handled_even_by_a_move_that_was_not_best():
    # Taking the attacker (WP 0.45, so 0.11 below the best) still deals with a threat worth 0.30.
    threat = {"move_san": "Nxf1+", "move_uci": "c6e4", "wp_if_ignored": 0.26, "swing": 0.30}
    m = compare_move(record(wp_after=0.45, threat=threat), win(), T)
    assert m["q3"]["status"] == "handled" and m["gap"] != "opponent_idea"


def test_costly_move_in_time_trouble():
    m = compare_move(record(wp_after=0.40, clock_before=12.0), win(), T)
    assert m["gap"] == "time_trouble"
    assert compare_move(record(wp_after=0.40, clock_before=95.0), win(), T)["gap"] == "no_thoughts"
