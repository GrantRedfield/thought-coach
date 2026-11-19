from thoughtcoach.tone import score, stats


def test_tone_of_what_you_say():
    assert score("I have a good position now")[0] == 1
    assert score("this is not good")[0] == -1
    assert score("not bad")[0] == 1
    assert score("he is in trouble")[0] == 1
    assert score("I'm worried about my king")[0] == -1
    assert score("a weak d6 pawn")[0] == 0  # a chess word, not a mood
    assert score("queen c4, queen c3")[0] == 0


def row(mood, costly, forcing_best=False, forcing_played=False):
    return {"mood": mood, "costly": costly, "tt": False, "wp_before": 0.5, "wp_after": 0.4 if costly else 0.5,
            "best_forcing": forcing_best, "played_forcing": forcing_played}


def test_no_finding_without_enough_moves():
    st = stats([row("upbeat", False)] * 3 + [row("worried", True)] * 3)
    assert st["findings"] == []


def test_a_real_gap_is_stated():
    rows = [row("upbeat", i % 10 == 0) for i in range(20)] + [row("worried", i % 2 == 0) for i in range(20)]
    st = stats(rows)
    assert st["findings"][0].startswith("You play better when you sound upbeat")
