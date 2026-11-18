import io

import chess
import chess.pgn

from thoughtcoach.insights import _phrase, build

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nd4 4. Nxe5 Qg5 5. Nxf7 Qxg2 *"


def move(ply, san, loss=0.0, best="Nxd4", drawn=None, wp_before=0.55):
    return {"ply": ply, "move_number": (ply + 1) // 2, "move_san": san, "wp_before": wp_before,
            "wp_after": wp_before - loss, "wp_loss": loss, "best_move_san": best, "own_time_s": 5.0,
            "gap": "missed_candidate" if loss >= 0.05 else None, "mistake": loss >= 0.05,
            "drawn_candidates": drawn or []}


def setup(windows):
    game = chess.pgn.read_game(io.StringIO(PGN))
    report = {"thresholds": {"mistake": 0.05}, "moves": [
        move(5, "Bc4"),
        move(7, "Nxe5", loss=0.30, drawn=[{"move_san": "Nxe5", "legal": True, "played": True, "first_drawn_s": 2.0}]),
        move(9, "Nxf7", wp_before=0.25),
    ]}
    return game, report, {"windows": windows}


def test_phrases():
    assert _phrase("Oh man, what a shot.") == ("shock", "what a shot")
    assert _phrase("let's go back, like this") is None
    assert _phrase("maybe I should have played rook")[0] == "regret"
    assert _phrase("Jackpot.") == ("confident", "jackpot")
    assert _phrase("He's hustling me on time.")[0] == "clock"


def test_called_it_and_one_track():
    weak = {"kind": "key_square", "source": "voice", "from": "g5", "said": "weak g5", "phase": "own_time"}
    marked_only = {"kind": "key_square", "source": "voice", "from": "e5", "said": "e5", "phase": "own_time"}
    game, report, thinking = setup([{"ply": 5, "shapes": [weak, marked_only]}])
    found = {i["kind"]: i for i in build(game, chess.WHITE, report, thinking)}
    assert "4. Nxe5 (-30%) let 4... Qg5 land on exactly that square" in found["called_it"]["body"]
    assert "said 'weak g5'" in found["called_it"]["body"]
    assert "Nxd4 (best) never came up" in found["one_track"]["body"]


def test_read_plan_and_planned():
    idea = lambda frm, to: {"kind": "opponent_idea", "source": "arrow", "from": frm, "to": to, "phase": "own_time"}  # noqa: E731
    plan = {"kind": "candidate", "source": "arrow", "from": "e5", "to": "f7", "phase": "own_time", "played": False, "legal": False}
    game, report, thinking = setup([{"ply": 5, "shapes": [idea("c6", "d4"), idea("d8", "g5")]},
                                    {"ply": 7, "shapes": [idea("g5", "g2"), plan]}])
    found = {i["kind"]: i for i in build(game, chess.WHITE, report, thinking)}
    assert "3... Nd4" in found["read_plan"]["body"] and "5... Qxg2" in found["read_plan"]["body"]
    assert "5. Nxf7 (0%) was considered 1 move early before it was even possible" in found["planned"]["body"]


def test_percent_format():
    from thoughtcoach.fmt import pct, spct
    assert (pct(0.534), pct(0.004), spct(-0.284), spct(0.0)) == ("53%", "0.4%", "-28%", "0%")


def test_verdict_picks_one_good_and_one_to_watch():
    from thoughtcoach.insights import verdict
    report = {
        "game": {"my_color": "white"},
        "thresholds": {"mistake": 0.05, "time_trouble": 30},
        "summary": {"q3_real_threats": 4, "q3_spotted": 3, "q3_handled": 1, "q4_best_considered": 2, "q4_positions": 6},
        "moves": [
            {**move(5, "Bc4", wp_before=0.50), "clock_before": 120},
            {**move(7, "Qe2", loss=0.08, best="d4", wp_before=0.60), "clock_before": 60,
             "drawn_candidates": [{"move_san": "Qe2", "legal": True, "played": True}], "gap": "missed_candidate"},
            {**move(9, "Qxf7+", loss=0.40, best="O-O", wp_before=0.90), "clock_before": 12, "gap": "time_trouble",
             "drawn_candidates": [{"move_san": "O-O", "legal": True, "played": False}]},
        ],
    }
    v = verdict(report, None, [])
    assert v["good"]["text"] == "You spotted 3 of the 4 real threats against you before they could happen."
    assert v["watch"]["ply"] == 9
    assert v["watch"]["text"].startswith("The clock. You were under 30s from move 5, and 5. Qxf7+ took your win "
                                         "chance from 90% to 50% with O-O (best) already considered.")


def test_hindsight_words_match_whole_words():
    from thoughtcoach.insights import HINDSIGHT_RE

    assert HINDSIGHT_RE.search("oh man, just dropped a piece")
    assert not HINDSIGHT_RE.search("can't exploit it yet though")


def test_feelings_are_not_move_evaluations():
    from thoughtcoach.insights import _phrase

    assert _phrase("I don't think 93 is great") is None  # judging a move (g3), not a feeling
    assert _phrase("I don't think I want to trade this beautiful bishop") is None
    assert _phrase("this is winning for me")[0] == "confident"
    assert _phrase("oh man, just dropped a piece")[0] == "shock"
