from thoughtcoach import story

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def move(n, san, loss, **extra):
    return {"ply": 2 * n - 1, "move_number": n, "move_san": san, "fen": FEN, "skipped": None, "clock_before": 200,
            "wp_before": 0.5, "wp_after": 0.5 - loss, "wp_loss": loss, "mistake": loss >= 0.05,
            "best_move_san": "Bb5", "drawn_candidates": [], "q1": {"weaknesses": []}, "q3": {}, "q4": {}, **extra}


def report(moves, by_gap):
    return {"game": {"id": "game0001", "white": "me", "black": "them", "my_color": "white", "result": "0-1",
                     "time_control": "600+0"},
            "thresholds": {"time_trouble": 30},
            "summary": {"moves_with_thoughts": 2, "by_gap": by_gap, "q1_list": [], "q2_list": []},
            "moves": moves, "insights": []}


def test_story_keeps_the_standout_moments_and_picks_a_focus():
    moves = [move(3, "Bc4", 0.30, drawn_candidates=[{"move_san": "Bc4", "legal": True, "played": True}]),
             move(4, "d3", 0.01, q3={"status": "not_drawn", "threat_san": "Nd4", "swing": 0.02})]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.30}, "opponent_idea": {"wp_lost": 0.0}})
    s = story.build(r, {"moves": []}, None, None)
    kinds = [b["kind"] for b in s["beats"]]
    assert kinds == ["turning"]  # the tiny threat (2%) doesn't make the cut
    assert "Bb5 never came up" in s["beats"][0]["body"]
    assert s["focus"]["key"] == "candidates" and s["focus"]["title"] == "Find a second candidate"
    page = story.render(s)
    assert page.count("class='slide'") == 3  # intro (with the result), the turning point, the focus
    assert "Your focus for the next game" in page


def test_history_can_tip_the_focus():
    moves = [move(3, "Bc4", 0.06)]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.03}, "opponent_idea": {"wp_lost": 0.02}})
    s = story.build(r, {"moves": []}, None, None, history={"share": {"q3": 0.6, "candidates": 0.0}, "note": {}})
    assert s["focus"]["key"] == "q3"


def test_a_game_decided_by_one_move_gets_a_short_story():
    moves = [move(3, "Bc4", 0.40), move(5, "d3", 0.06), move(6, "h3", 0.02, q3={"status": "spotted", "threat_san": "Nd4", "swing": 0.5})]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.46}})
    s = story.build(r, {"moves": []}, None, None)
    assert s["decided_by"] == "3. Bc4" and len(s["beats"]) <= 2
    assert "It came down to one move: 3. Bc4." in story.render(s)
