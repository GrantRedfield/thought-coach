import chess

from thoughtcoach import questions

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def game(gid="game0001"):
    move = {"ply": 5, "move_number": 3, "move_san": "Bc4", "fen": FEN, "skipped": None, "clock_before": 120,
            "wp_loss": 0.08, "mistake": True, "best_move_san": "Bb5",
            "drawn_candidates": [{"move_san": "Bc4", "legal": True}, {"move_san": "d4", "legal": True}],
            "q3": {"status": "not_drawn", "threat_san": "Nd4", "swing": 0.15}, "q4": {"engine": ["Bb5", "d4"]}}
    report = {"game": {"id": gid, "date": "2026.11.17", "my_color": "white"}, "thresholds": {"time_trouble": 30},
              "summary": {"moves_with_thoughts": 1,
                          "q1_list": [{"square": "d5", "kind": "hole", "side": "theirs", "you": "marked", "move": 3, "you_move": 3}],
                          "q2_list": [{"square": "c1", "piece": "bishop", "activity": 0.1, "you": None, "move": 3}]},
              "moves": [move]}
    return {"id": gid, "report": report}


def test_each_question_has_counts_and_example_positions():
    q = {x["key"]: x for x in questions.compute([game()])}
    assert (q["q1"]["found"], q["q1"]["total"]) == (1, 1) and q["q1"]["examples"][0]["seen"]
    assert (q["q2"]["found"], q["q2"]["total"]) == (0, 1) and not q["q2"]["examples"][0]["seen"]
    assert (q["q3"]["found"], q["q3"]["total"]) == (0, 1)
    assert q["q3"]["examples"][0]["arrows"][0].tail == chess.C6  # their knight's threat Nd4
    assert (q["q4"]["found"], q["q4"]["total"]) == (0, 1)
    assert "Not among your candidates" in q["q4"]["examples"][0]["note"]
    page = questions.render(questions.compute([game()]))
    assert "Which piece is doing least?" in page and "/games/game0001" in page
