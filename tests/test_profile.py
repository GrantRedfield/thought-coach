import json

from thoughtcoach import profile


def fake_game(out, gid, color, result, moves):
    """moves: (move number, seconds thought, win % lost, candidates drawn, clock left)."""
    rows = []
    for n, secs, loss, cands, clock in moves:
        rows.append({"ply": 2 * n - (color == "white"), "move_number": n, "move_san": "Nf3", "skipped": None,
                     "wp_before": 0.5, "wp_after": 0.5 - loss, "wp_loss": loss, "mistake": loss >= 0.05,
                     "gap": ("time_trouble" if clock < 30 else "missed_candidate") if loss >= 0.05 else None,
                     "own_time_s": secs, "time_spent_pgn": secs, "clock_before": clock,
                     "drawn_candidates": [{"legal": True}] * cands})
    report = {"game": {"id": gid, "date": "2026.11.16", "my_color": color, "result": result},
              "thresholds": {"time_trouble": 30},
              "summary": {"moves_with_thoughts": 1, "q3_spotted": 2, "q3_handled": 1, "q3_real_threats": 4,
                          "q4_best_considered": 3, "q4_positions": 10, "q1_found": 1, "q1_weaknesses": 6,
                          "q2_found": 0, "q2_positions": 2},
              "moves": rows}
    (out / f"{gid}.gaps.json").write_text(json.dumps(report))
    (out / f"{gid}.json").write_text(json.dumps({"game": {}, "moves": []}))


def test_patterns_need_many_games_and_say_how_many_moves(tmp_path):
    out, games = tmp_path / "out", tmp_path / "games"
    out.mkdir()
    games.mkdir()
    # Quick moves (1s) often costly; slow ones (12s) rarely. Under 30s on the clock = time trouble.
    for i in range(4):
        moves = [(10 + k, 1, 0.08 if k % 2 else 0.0, 1, 120) for k in range(10)]
        moves += [(20 + k, 12, 0.0, 2, 90) for k in range(10)]
        moves += [(35, 2, 0.30, 0, 10)]
        fake_game(out, f"game000{i}", "white" if i % 2 else "black", "1-0", moves)
    p = profile.compute(profile.load(out, games))
    assert p["moves"] == 84 and p["tt"]["moves"] == 4
    think = dict(p["think"])
    assert think["under 3s"]["rate"] == 0.5 and think["8-20s"]["rate"] == 0.0
    strong, weak = profile.findings(p)
    heads = [f["head"] for f in weak]
    assert "Quick moves go wrong" in heads and "The clock costs you most" in heads
    quick = next(f for f in weak if f["head"] == "Quick moves go wrong")
    assert quick["n"] == 40 and not quick["early"]
    page = profile.render(p)
    assert "Patterns across your games" in page and "Costly moves by think time" in page


def test_one_game_shows_no_patterns(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    fake_game(out, "game0000", "white", "1-0", [(10, 1, 0.1, 1, 100)])
    assert profile.render(profile.compute(profile.load(out, tmp_path))) == ""


def test_serious_is_rapid_or_slower():
    from thoughtcoach import profile

    def g(tc):
        return {"report": {"game": {"time_control": tc}}}

    assert profile.serious(g("600+0")) and profile.serious(g("300+5")) and profile.serious(g(None))
    assert not profile.serious(g("300+3")) and not profile.serious(g("180+0"))
