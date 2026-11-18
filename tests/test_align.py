import copy
from pathlib import Path

import chess
import chess.pgn

from thoughtcoach.align import align, classify, fold_circles, match_positions, render
from thoughtcoach.arrows import load_recordings

DATA = Path(__file__).parent / "data"


def load():
    (recording,) = load_recordings(DATA / "arrows_sample.json")
    with open(DATA / "arrows_sample.pgn", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    return game, recording


def window(result, ply):
    return next(w for w in result["windows"] if w["ply"] == ply)


def test_shapes_are_classified_by_whose_piece_they_start_on():
    game, recording = load()
    result = align(game, recording, chess.WHITE)
    nf3 = window(result, 3)
    assert nf3["move_san"] == "Nf3"
    kinds = [(s["kind"], s["from"], s["to"], s["move_san"], s["played"]) for s in nf3["shapes"]]
    assert kinds == [
        ("candidate", "g1", "f3", "Nf3", True),  # my knight: the move I played
        ("opponent_idea", "d8", "h4", "Qh4", None),  # their queen: read as their move after a null move
        ("key_square", "f7", None, None, None),  # circle
    ]
    assert nf3["shapes"][0]["phase"] == "own_time"
    assert nf3["shapes"][0]["drawn_s"] == 1.5  # 1.5 s after 1...e5 appeared
    assert nf3["shapes"][0]["on_board_s"] == 3.5


def test_window_times():
    game, recording = load()
    result = align(game, recording, chess.WHITE)
    nf3 = window(result, 3)
    assert nf3["own_time_s"] == 5.1  # 1...e5 seen at +3.0 s, 2.Nf3 seen at +8.1 s
    assert nf3["opponent_time_s"] == 2.0  # 1.e4 seen at +1.0 s
    assert nf3["time_spent_pgn"] == 6.0  # 3:00 -> 2:54
    assert result["positions_missed"] == []
    assert "candidate" in render(result)


def test_skipped_position_and_opponent_time():
    """A premove skips 1...e5; an arrow drawn while Black was thinking belongs to my 2nd move."""
    game, recording = load()
    rec = copy.deepcopy(recording)
    after_e4 = rec["events"][1]["placement"]
    rec["events"] = [e for e in rec["events"] if e["type"] != "shape_add" and e["type"] != "shape_remove"]
    rec["events"] = [e for e in rec["events"] if not (e["type"] == "position" and e["positions_seen"] == 3)]
    rec["events"].insert(
        2,
        {"t": rec["events"][1]["t"] + 500, "type": "shape_add", "from": "g1", "to": "f3", "brush": "green",
         "positions_seen": 2, "placement": after_e4},
    )
    result = align(game, rec, chess.WHITE)
    assert result["positions_missed"] == [2]
    nf3 = window(result, 3)
    (shape,) = nf3["shapes"]
    assert shape["phase"] == "opponent_time"
    assert shape["move_san"] == "Nf3" and shape["played"]
    assert nf3["own_time_s"] is None  # the position before my move was never on screen


def test_match_positions_in_order_with_repetition():
    a, b = "A", "B"
    events = [{"type": "position", "t": t, "placement": p} for t, p in [(0, a), (1, b), (2, a), (3, b)]]
    seen, on_screen = match_positions(events, [a, b, a, b])
    assert seen == {0: 0, 1: 1, 2: 2, 3: 3}
    assert on_screen == [0, 1, 2, 3]


def test_circle_right_after_arrow_is_folded_into_it():
    arrow = {"kind": "candidate", "from": "c1", "to": "e3", "drawn_t": 1000}
    circle = {"kind": "key_square", "from": "e3", "to": None, "drawn_t": 1150}
    late_circle = {"kind": "key_square", "from": "e3", "to": None, "drawn_t": 5000}
    other_circle = {"kind": "key_square", "from": "f7", "to": None, "drawn_t": 1200}
    kept = fold_circles([arrow, circle, other_circle, late_circle])
    assert kept == [arrow, other_circle, late_circle]
    assert arrow["target_circled"]


def test_classify():
    knight_w = chess.Piece(chess.KNIGHT, chess.WHITE)
    assert classify(knight_w, True, chess.WHITE) == "candidate"
    assert classify(knight_w, True, chess.BLACK) == "opponent_idea"
    assert classify(None, True, chess.WHITE) == "route"
    assert classify(None, False, chess.WHITE) == "key_square"


def test_picked_up_pieces():
    import copy
    from thoughtcoach.gaps import Thresholds, compare_move
    (recording,) = load_recordings(DATA / "arrows_sample.json")
    with open(DATA / "arrows_sample.pgn", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    rec = copy.deepcopy(recording)
    t = rec["events"][2]["t"]  # 1...e5 appeared; next is my 2. Nf3
    rec["events"][3:3] = [{"type": "pick_up", "square": "f1", "t": t + 100}, {"type": "put_down", "square": "f1", "t": t + 900},
                          {"type": "pick_up", "square": "g1", "t": t + 1000}]
    rec["events"].sort(key=lambda e: e["t"])
    window = next(w for w in align(game, rec, chess.WHITE)["windows"] if w["ply"] == 3)
    picks = [(s["piece"], s["from"], s["played"], s["on_board_s"]) for s in window["shapes"] if s["kind"] == "picked_up"]
    assert picks == [("B", "f1", False, 0.8), ("N", "g1", True, 4.1)]  # held until Nf3 landed
    record = {"ply": 3, "move_number": 2, "fen": window["fen"], "move_san": "Nf3", "move_uci": "g1f3", "wp_before": 0.55,
              "wp_after": 0.45, "wp_loss": 0.10, "best_move_san": "Bc4", "skipped": None, "threat": None,
              "candidates": [{"move_san": "Bc4", "move_uci": "f1c4", "wp": 0.55}]}
    m = compare_move(record, window, Thresholds(mistake=0.10))
    assert [(p["square"], p["moved"], p["best_piece"]) for p in m["picked_up"]] == [("f1", False, True), ("g1", True, False)]
    assert m["gap"] != "no_thoughts"


def test_lingering_highlight_is_not_a_pickup():
    import copy
    (recording,) = load_recordings(DATA / "arrows_sample.json")
    with open(DATA / "arrows_sample.pgn", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    rec = copy.deepcopy(recording)
    # The position right after my 2. Nf3: lichess still highlights f3 straight after the move landed.
    nf3 = next(e for e in rec["events"] if e["type"] == "position" and e["placement"].startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2"))
    rec["events"].append({"type": "pick_up", "square": "f3", "t": nf3["t"] + 200})
    rec["events"].sort(key=lambda e: e["t"])
    windows = align(game, rec, chess.WHITE)["windows"]
    assert not any(s["kind"] == "picked_up" for w in windows for s in w["shapes"])
