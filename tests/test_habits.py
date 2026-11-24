from thoughtcoach import habits


def threat(status, san="Nd4", clock=None):
    return {"q3": {"status": status, "threat_san": san}, "clock_before": clock}


def game(gid, through, real, quiet_named=0, quiet=0):
    """`real` threats, `through` of them not stopped; `quiet` of them quiet (not captures), `quiet_named` drawn."""
    moves = []
    for i in range(real):
        q = i < quiet
        status = "spotted" if q and i < quiet_named else ("not_drawn" if i >= real - through else "handled")
        moves.append(threat(status, "Nd4" if q else "Bxf7"))
    return {"id": gid, "report": {"summary": {}, "thresholds": {"time_trouble": 30}, "moves": moves}, "analysis": {}}


def test_a_focus_measured_before_and_since():
    games = [game("a", 4, 10), game("b", 3, 10), game("c", 0, 10), game("d", 1, 10)]
    focus = {"key": "q3", "title": "Ask: what does my opponent want?"}
    stories = {"b": {"focus": focus}, "c": {"focus": focus}}  # given after b and after c
    [e] = habits.compute(games, stories)
    assert e["before"] == (7, 20) and e["since"] == (1, 20) and e["games"] == 2
    assert e["p"] > 0.95 and "Working" in e["text"]  # fewer let through: lower is better


def test_one_game_is_too_early():
    games = [game("a", 2, 10), game("b", 3, 10)]
    [e] = habits.compute(games, {"a": {"focus": {"key": "q3", "title": "t"}}})
    assert "Too early to tell after one game" in e["text"]


def test_this_game_line():
    line = habits.this_game("q3", game("c", 1, 4), [game("a", 1, 10)])
    assert line == "This game: real threats left unstopped, 1 of 4 (25%); in your games before: 1 of 10 (10%)."


def test_answered_without_a_word_counts_as_stopped():
    g = game("a", 0, 5)  # all handled, none named
    assert habits.MEASURE["q3"][1](g) == (0, 5)


def test_quiet_threats_named():
    g = game("a", 0, 6, quiet_named=1, quiet=2)  # 2 quiet threats, 1 drawn; 4 captures don't count
    assert habits.MEASURE["q3_quiet"][1](g) == (1, 2)


def test_time_trouble_threats_left_out():
    g = game("a", 1, 3)
    g["report"]["moves"][-1]["clock_before"] = 10  # the unstopped one came in time trouble
    assert habits.MEASURE["q3"][1](g) == (0, 2)
