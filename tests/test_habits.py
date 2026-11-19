from thoughtcoach import habits


def game(gid, spotted, real):
    return {"id": gid, "report": {"summary": {"q3_spotted": spotted, "q3_real_threats": real}}, "analysis": {}}


def test_a_focus_measured_before_and_since():
    games = [game("a", 1, 10), game("b", 2, 10), game("c", 6, 10), game("d", 7, 10)]
    focus = {"key": "q3", "title": "Ask: what does my opponent want?"}
    stories = {"b": {"focus": focus}, "c": {"focus": focus}}  # given after b and after c
    [e] = habits.compute(games, stories)
    assert e["before"] == (3, 20) and e["since"] == (13, 20) and e["games"] == 2
    assert e["p"] > 0.95 and "Working" in e["text"]


def test_one_game_is_too_early():
    games = [game("a", 2, 10), game("b", 3, 10)]
    [e] = habits.compute(games, {"a": {"focus": {"key": "q3", "title": "t"}}})
    assert "Too early to tell after one game" in e["text"]


def test_this_game_line():
    line = habits.this_game("q3", game("c", 3, 4), [game("a", 1, 10)])
    assert line == "This game: threats spotted before they happened, 3 of 4 (75%); in your games before: 1 of 10 (10%)."
