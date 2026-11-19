from thoughtcoach.fmt import acc, accuracy, move_accuracy


def test_accuracy_is_lichess_scale():
    assert move_accuracy(0) == 100.0
    assert 40 < move_accuracy(0.2) < 45          # a 20% drop scores like a lichess mistake
    assert accuracy([0, 0, 0]) == 1.0
    assert accuracy([]) is None
    # one blunder pulls a game down more than its share of a plain average
    assert accuracy([0.0] * 9 + [0.5]) < 0.85
    assert acc(0.873) == "87%" and acc(None) == "?"
