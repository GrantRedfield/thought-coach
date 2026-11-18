import json
from pathlib import Path

import chess
import pytest

from thoughtcoach.arrows import load_recordings, placement_change, summarise

SAMPLE = Path(__file__).parent / "data" / "arrows_sample.json"


def test_load_single_game():
    (game,) = load_recordings(SAMPLE)
    assert game["game_id"] == "AbCd1234"
    adds = [e for e in game["events"] if e["type"] == "shape_add"]
    assert [(e["from"], e["to"]) for e in adds] == [("g1", "f3"), ("d8", "h4"), ("f7", None)]


def test_load_bundle(tmp_path):
    game = json.loads(SAMPLE.read_text(encoding="utf-8"))
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"format": "thoughtcoach-arrows-bundle/1", "games": [game, game]}))
    assert len(load_recordings(bundle)) == 2


def test_rejects_other_json(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "something-else", "events": []}))
    with pytest.raises(ValueError):
        load_recordings(other)


def test_placement_change():
    start = chess.Board()
    after = start.copy()
    after.push_san("Nf3")
    assert placement_change(start.board_fen(), after.board_fen()) == "g1 -> f3"
    castle = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    castled = castle.copy()
    castled.push_san("O-O")
    assert placement_change(castle.board_fen(), castled.board_fen()) == "e1/h1 -> f1/g1"


def test_recording_positions_match_real_chess_positions():
    (game,) = load_recordings(SAMPLE)
    board = chess.Board()
    expected = [board.board_fen()]
    for san in ["e4", "e5", "Nf3"]:
        board.push_san(san)
        expected.append(board.board_fen())
    seen = [e["placement"] for e in game["events"] if e["type"] == "position"]
    assert seen == expected


def test_summary_mentions_every_shape():
    (game,) = load_recordings(SAMPLE)
    text = summarise(game)
    assert "2 arrows and 1 circles drawn" in text
    assert "+ arrow  g1->f3" in text
    assert "+ circle f7" in text
    assert "(green)" not in text  # default colour is not worth printing
    assert "game over" in text
