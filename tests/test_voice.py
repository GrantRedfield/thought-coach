"""Voice: parsing what was said, splitting clauses, and turning mentions into moves. No models needed."""

from pathlib import Path

import chess
import chess.pgn
import pytest

from thoughtcoach.align import align, spoken_moves
from thoughtcoach.arrows import load_recordings
from thoughtcoach.voice import find_mentions, tag_by_rules, tokens, utterances, voice_events

DATA = Path(__file__).parent / "data"


def one(text):
    (m,) = find_mentions(text)
    return {k: v for k, v in m.items() if v not in (None, False) and k != "said"}


def test_mishearings_are_normalised():
    assert tokens("Night E four") == ["knight", "e4"]
    assert tokens("castle short") == ["castles", "kingside"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("knight f3", {"piece": "N", "to": "f3"}),
        ("bishop takes e5", {"piece": "B", "to": "e5", "capture": True}),
        ("bishop c1 to e3", {"piece": "B", "from": "c1", "to": "e3"}),
        ("e2 e4", {"from": "e2", "to": "e4"}),
        ("I'll push d4", {"piece": "P", "to": "d4"}),
        ("takes on f7", {"to": "f7", "capture": True}),
        ("the d5 square", {"to": "d5", "square_only": True}),
        ("castle long", {"piece": "K", "castle": "Q"}),
    ],
)
def test_find_mentions(text, expected):
    assert one(text) == expected


def test_two_mentions_in_one_sentence_stay_apart():
    ms = find_mentions("queen h4 the f7 square")
    assert [(m["piece"], m["to"]) for m in ms] == [("Q", "h4"), (None, "f7")]


def test_tags_by_rules():
    assert tag_by_rules("I could play knight f3", find_mentions("knight f3")[0]) == "candidate"
    assert tag_by_rules("he wants queen h4", find_mentions("queen h4")[0]) == "opponent_idea"
    assert tag_by_rules("the f7 square is weak", find_mentions("the f7 square is weak")[0]) == "weakness"


def test_clauses_split_at_punctuation():
    words = [{"t": 1000 + i * 100, "end_t": 0, "word": w}
             for i, w in enumerate(["I", "could", "play", "knight", "f3,", "he", "wants", "queen", "h4."])]
    transcript = {"segments": [{"t": 1000, "end_t": 2000, "text": "x", "words": words}]}
    assert [c["text"] for c in utterances(transcript)] == ["I could play knight f3,", "he wants queen h4."]
    events = voice_events(transcript)
    assert [(e["tag"], e["to"], e["t"]) for e in events] == [("candidate", "f3", 1300), ("opponent_idea", "h4", 1700)]


def test_spoken_moves_disambiguation():
    board = chess.Board()
    assert [board.san(m) for m in spoken_moves(board, {"piece": "N", "to": "f3"})] == ["Nf3"]
    board = chess.Board("4k3/8/8/8/8/8/8/1N3N1K w - - 0 1")  # two knights can reach d2
    assert sorted(board.san(m) for m in spoken_moves(board, {"piece": "N", "to": "d2"})) == ["Nbd2", "Nfd2"]


def test_voice_mentions_join_the_thinking_window():
    (recording,) = load_recordings(DATA / "arrows_sample.json")
    with open(DATA / "arrows_sample.pgn", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    t_e5 = recording["events"][2]["t"]  # 1...e5 appeared
    words = [{"t": t_e5 + 500 + i * 200, "end_t": 0, "word": w}
             for i, w in enumerate(["night", "f3,", "he", "wants", "queen", "h4,", "f7", "is", "weak."])]
    spoken = voice_events({"segments": [{"t": t_e5 + 500, "end_t": 0, "text": "x", "words": words}]})
    result = align(game, recording, chess.WHITE, spoken)
    nf3 = next(w for w in result["windows"] if w["ply"] == 3)
    said = [(s["kind"], s["move_san"], s["from"], s["played"]) for s in nf3["shapes"] if s["source"] == "voice"]
    assert said == [
        ("candidate", "Nf3", "g1", True),
        ("opponent_idea", "Qh4", "d8", False),
        ("key_square", None, "f7", False),
    ]


def test_which_piece_by_file():
    assert one("rook ae1") == {"piece": "R", "to": "e1", "from_file": "a"}
    board = chess.Board("7k/8/8/8/8/8/8/R4RK1 w - - 0 1")
    assert [board.san(m) for m in spoken_moves(board, find_mentions("rook ae1")[0])] == ["Rae1"]


def test_resolving_spoken_moves():
    from thoughtcoach.align import resolve_voice
    board = chess.Board("r6k/8/8/8/8/8/8/R4RK1 w - - 0 1")
    rook_e1 = {**find_mentions("rook e1")[0], "tag": "candidate", "tagger": "llm"}
    assert resolve_voice(rook_e1, chess.WHITE, board, board)["ambiguous"] == ["Rae1", "Rfe1"]
    assert resolve_voice(rook_e1, chess.WHITE, board, board, chess.Move.from_uci("f1e1"))["move_san"] == "Rfe1"
    rook_e8 = {**find_mentions("rook e8")[0], "tag": "candidate", "tagger": "llm"}  # only Black can
    assert resolve_voice(rook_e8, chess.WHITE, board, board)["kind"] == "opponent_idea"
    bare = {**find_mentions("the rook")[0], "tag": "candidate", "tagger": "llm"}
    assert resolve_voice(bare, chess.WHITE, board, board)["kind"] == "other"


def test_clauses_split_at_long_pauses():
    words = [{"t": 0, "end_t": 300, "word": "He"}, {"t": 300, "end_t": 600, "word": "has"},
             {"t": 30000, "end_t": 30300, "word": "a"}, {"t": 30300, "end_t": 30600, "word": "weak"},
             {"t": 30600, "end_t": 30900, "word": "d6"}]
    clauses = utterances({"segments": [{"t": 0, "end_t": 31000, "text": "x", "words": words}]})
    assert [(c["text"], c["t"]) for c in clauses] == [("He has", 0), ("a weak d6", 30000)]


def test_worst_piece_needs_the_words_for_it():
    from thoughtcoach.voice import guard

    rook = {"said": "rook", "square_only": False, "piece_only": True}
    assert guard("worst_piece", "rook", rook) != "worst_piece"
    assert guard("worst_piece", "my rook is my worst piece", rook) == "worst_piece"
    assert guard("candidate", "rook", rook) == "candidate"


def test_a_piece_said_with_worst_words_is_a_worst_piece():
    from thoughtcoach.voice import find_mentions, guard

    [bishop] = find_mentions("The bishop sucks.")
    assert guard("candidate", "The bishop sucks.", bishop) == "worst_piece"
