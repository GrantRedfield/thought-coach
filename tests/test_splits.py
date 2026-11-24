from thoughtcoach import splits


def _move(ply, wp, loss, *, clock=300, fen="r3k2r/8/8/8/8/8/8/R3K2R w - - 0 30", q3=None, cands=("Ra7",), found=True):
    return {"ply": ply, "move_number": ply // 2 + 1, "fen": fen, "wp_before": wp, "wp_loss": loss, "clock_before": clock,
            "q3": {"status": q3} if q3 else {}, "drawn_candidates": [{"move_san": c} for c in cands],
            "q4": {"engine": ["Ra7"], "found": ["Ra7"] if found else []}}


def _game(moves, times, opp=None, board_read=False):
    return {"report": {"thresholds": {"time_trouble": 30}, "moves": moves},
            "analysis": {"moves": [{"ply": m["ply"], "time_spent": t, "opponent_move_san": (opp or {}).get(m["ply"], "a6")}
                                   for m, t in zip(moves, times)]},
            "board_read": board_read}


def test_buckets_and_counts():
    moves = [_move(40, 0.8, 0.0, q3="handled"), _move(42, 0.5, 0.2), _move(44, 0.5, 0.0, q3="not_drawn"),
             _move(46, 0.2, 0.0, clock=10)]
    t = splits.of_games([_game(moves, [10, 20, 30, 5], opp={44: "Bxe5+"})])
    assert t["situation"]["ahead"]["q3"] == [0, 1]
    assert t["situation"]["level"]["costly"] == [1, 2]
    assert "behind" not in t["situation"]  # the only losing move was in time trouble
    assert t["after_slip"]["after"]["q3"] == [1, 1]  # the move after the costly one
    assert t["their_move"]["forcing"]["q3"] == [1, 1]
    assert t["phase"]["endgame"]["shown"][1] == 3
    assert abs(t["phase"]["all"]["think"][0] / 3 - 1) < 0.01  # against this game's average


def test_board_read_games_skip_win_chance_splits():
    t = splits.of_games([_game([_move(40, 0.8, 0.3)], [10], board_read=True)])
    assert "situation" not in t and "after_slip" not in t
    assert "costly" not in t["phase"]["endgame"]


def test_findings_compare_your_change_with_masters():
    yours = {"situation": {"behind": {"q3": [10, 40]}, "all": {"q3": [12, 140]}}}
    ms = {"situation": {"behind": {"q3": {"rate": 0.05, "players": 5, "n": 99}},
                        "all": {"q3": {"rate": 0.04, "players": 5, "n": 4000}}}}
    f = splits.findings(yours, ms)
    assert len(f) == 1 and f[0]["worse"] is True
    assert "When you're losing" in f[0]["text"] and "Masters: 4% → 5%" in f[0]["text"]
    # too few of your moves: nothing said
    assert splits.findings({"situation": {"behind": {"q3": [3, 10]}, "all": {"q3": [5, 110]}}}, ms) == []


def test_master_splits_need_cases_per_player():
    a = {"phase": {"endgame": {"cands": [40, 20]}}}
    b = {"phase": {"endgame": {"cands": [10, 5]}}}  # too few: left out
    m = splits.master_splits([a, b])
    assert m["phase"]["endgame"]["cands"] == {"rate": 2.0, "players": 1, "n": 20}


def test_section_renders_without_masters():
    moves = [_move(40 + 2 * i, 0.5, 0.0) for i in range(30)]
    s = splits.section([_game(moves, [10] * 30)], ms={})
    assert "How your thinking changes" in s and "<table>" in s
