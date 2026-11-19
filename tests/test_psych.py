from thoughtcoach import psych


def test_phrases():
    assert psych.DEFEAT.search("i'm just not finding a single move without completely losing")
    assert psych.DEFEAT.search("white's probably winning, but okay")
    assert not psych.DEFEAT.search("i think i'm fine here")
    assert psych.JUDGING.search("why did i play that")
    assert not psych.JUDGING.search("this weakens my knight")  # noticing, not judging


class FakeGame:
    def __init__(self, mine):
        self.mine = mine

    def label(self, ply):
        n = (ply + 1) // 2
        return f"{n}. X" if ply % 2 else f"{n}... X"


def test_the_tide_turns_and_you_give_it_back():
    mine = {56: {"wp_before": 0.25, "wp_after": 0.20, "wp_loss": 0.05},
            58: {"wp_before": 0.43, "wp_after": 0.09, "wp_loss": 0.34}}
    [i] = psych.tide(FakeGame(mine), None)
    assert i["kind"] == "tide" and i["ply"] == 57 and not i["took"]
    assert "from 20% to 43%" in i["body"] and "gave it back (43% → 9%)" in i["body"]


def test_denying_it_is_not_giving_up():
    from thoughtcoach.psych import defeated
    assert defeated("I'm lost here")
    assert defeated("white is winning")
    assert not defeated("I don't think I'm lost")
    assert not defeated("I'm not sure, but it's not over, not busted")
    assert not defeated("no, I'm not losing")
