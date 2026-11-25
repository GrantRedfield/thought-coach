"""The coaching summary (coach.py) and the review page (review.py)."""

import io
import json
import re

import chess.pgn

from thoughtcoach import coach, review


def _move(ply, san, uci, fen, wp_before, wp_after, *, gap=None, drawn=(), rejected=(), reply=None, q3=None):
    loss = round(max(0.0, wp_before - wp_after), 3)
    return {"ply": ply, "move_number": (ply + 1) // 2, "move_san": san, "fen": fen, "wp_before": wp_before, "wp_after": wp_after,
            "wp_loss": loss, "mistake": loss >= 0.05, "gap": gap if loss >= 0.05 else None, "best_move_san": "Nc3",
            "drawn_candidates": [{"move_san": s, "move_uci": u, "legal": True} for s, u in drawn],
            "drawn_opponent_ideas": [], "key_squares": [], "q3": q3 or {"status": "none"},
            "q4": {"found": [s for s, _ in drawn], "wrongly_rejected": list(rejected)}, "reply": reply, "clock_before": 300}


def _game():
    g = chess.pgn.read_game(io.StringIO("1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 *"))
    b = g.board(); fens = []
    for mv in g.mainline_moves():
        fens.append(b.fen()); b.push(mv)
    return g, fens


def _report():
    g, fens = _game()
    moves = [
        _move(3, "Nf3", "g1f3", fens[2], 0.55, 0.40, gap="wrongly_rejected", drawn=[("Nc3", "b1c3"), ("Nf3", "g1f3")],
              rejected=["Nc3"], reply={"san": "Nc6", "uci": "b8c6", "recorded": True}),
        _move(5, "Bc4", "f1c4", fens[4], 0.45, 0.30, gap="wrongly_rejected", drawn=[("Nc3", "b1c3")],
              rejected=["Nc3"], reply={"san": "Nf6", "uci": "g8f6", "recorded": True}),
    ]
    report = {"game": {"id": "AbCd1234", "white": "Me", "black": "Them", "my_color": "white", "result": "0-1", "time_control": "600+0"},
              "thresholds": {"time_trouble": 30}, "moves": moves,
              "summary": {"moves_reviewed": 2, "moves_with_thoughts": 2, "q4_best_considered": 2, "q4_positions": 2,
                          "q3_real_threats": 0, "q3_spotted": 0, "q3_handled": 0, "by_gap": {}}}
    analysis = {"moves": [{"ply": m["ply"], "move_number": m["move_number"], "move_san": m["move_san"], "move_uci": u, "fen": m["fen"],
                           "candidates": [{"move_san": "Nc3", "move_uci": "b1c3", "wp": m["wp_before"], "line_san": "Nc3 Nf6"}],
                           "played_line_san": f"{m['move_san']} {m['reply']['san']}"}
                          for m, u in zip(moves, ("g1f3", "f1c4"))]}
    return report, analysis, g


def test_the_focus_comes_from_the_analysis_not_from_a_template():
    report, analysis, _ = _report()
    s = coach.summary(report, {})
    assert s["focus"]["key"] == "test_reply"
    assert "In both of your costly decisions" in s["focus"]["why"] and "(Nc6, Nf6)" in s["focus"]["why"]
    # a small sample shows the count and says so, not a percentage
    best = next(x for x in s["stats"] if x["label"].startswith("Best move"))
    assert best["value"] == "2 of 2" and best["small"]
    # no threats this game: "none", not a 0% that would read as poor play
    rec = next(x for x in s["stats"] if "recorded thinking" in x["label"])
    assert rec["value"] == "none this game"


def test_recorded_and_answered_are_separate():
    report, _, _ = _report()
    for m in report["moves"]:  # two real threats, not recorded; one move held, the other was costly
        m["q3"] = {"status": "not_drawn", "threat_san": "Qh4"}
    report["summary"].update(q3_real_threats=2, q3_spotted=0)
    report["moves"][0]["wp_after"] = 0.55; report["moves"][0]["mistake"] = False
    s = coach.summary(report, {})
    vals = {x["label"]: x["value"] for x in s["stats"]}
    assert vals["Their threats in your recorded thinking"] == "0 of 2" and vals["Your move held against their threat"] == "1 of 2"


def test_time_controls_read_like_lichess():
    assert coach.human_tc("600+0") == "10+0 rapid" and coach.human_tc("180+2") == "3+2 blitz" and coach.human_tc("1800+30") == "30+30 classical"


def test_one_board_and_every_position_in_the_page():
    report, analysis, game = _report()
    page = review.render(report, analysis, None, game=game)
    assert page.count("<svg class=\"board\"") == 1  # one board for the whole page
    data = json.loads(re.search(r'<script type="application/json" id="rvdata">(.*?)</script>', page, re.S).group(1))
    assert len(data["fens"]) == 7 and data["sans"][2] == "2.Nf3"
    d = data["dec"]["2"]  # the position before 2. Nf3
    assert d["best"] == "b1c3" and d["reply"]["san"] == "Nc6" and d["played_line"][0]["san"] == "2.Nf3"
    assert any(k == "lesson" for k, _ in d["coach"])
    assert 'data-pos="2"' in page or "data-pos='2'" in page  # the decisions that mattered open their position
    assert "Thinking Report" in page and "Game Story" in page and "10+0 rapid" in page


def test_no_recorded_thinking_means_no_claim_about_it():
    report, _, _ = _report()
    report["summary"]["moves_with_thoughts"] = 0
    f = coach.summary(report, {})["focus"]
    assert f["key"] == "record" and "can only say which moves were costly, not why" in f["why"]
