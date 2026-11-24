from thoughtcoach import practice


def game(gid, moves, color="black"):
    return {"id": gid, "report": {"game": {"my_color": color, "time_control": "600+0"}, "thresholds": {"time_trouble": 30},
                                  "moves": moves}}


FEN = "r2q1rk1/pp2bppp/n1p1pnb1/8/2PPp1P1/P1N1B2P/1PQ1NP2/2KR1B1R b - - 0 11"


def mv(**kw):
    return {"fen": FEN, "move_san": "Rc8", "move_number": 11, "ply": 22, "wp_loss": 0.2, "wp_before": 0.5, "wp_after": 0.3,
            "best_move_san": "b5", "clock_before": 200, **kw}


def test_kinds_and_focus_order():
    g = game("a", [mv(q3={"status": "not_drawn", "threat_san": "Nf4", "swing": 0.2}),
                   mv(gap="wrongly_rejected", wp_loss=0.1)])
    ps = practice.positions([g], focus="trust")
    assert [p["kind"] for p in ps] == ["dropped", "threat"]  # the current focus comes first
    assert "Nf4" in ps[1]["a"] and "White threaten" in ps[1]["q"]


def test_small_losses_and_time_trouble_are_left_out():
    g = game("a", [mv(wp_loss=0.01, gap="missed_candidate"), mv(gap="missed_candidate", clock_before=5)])
    assert practice.positions([g]) == []
    assert "None yet" in practice.section([g])


def test_section_renders_a_board_and_answer():
    html = practice.section([game("a", [mv(gap="missed_candidate", q4={"engine": ["b5", "Qb6", "Nc7"]})])])
    assert "<svg" in html and "Find three candidate moves" in html and "Qb6, Nc7" in html
