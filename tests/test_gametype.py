from thoughtcoach.gametype import classify


def analysis(points):
    """Your moves with the win chance before and after each."""
    return {"moves": [{"ply": 2 * i + 1, "move_san": "a3", "wp_before": b, "wp_after": a}
                      for i, (b, a) in enumerate(points)]}


def kind(points, result="1-0", color="white"):
    return classify(analysis(points), {"result": result, "my_color": color})["kind"]


LEVEL = [(0.5, 0.5)] * 6


def test_swindles_both_ways():
    assert kind(LEVEL + [(0.3, 0.1), (0.1, 0.2), (0.4, 0.9)]) == "swindle"
    assert kind(LEVEL + [(0.6, 0.9), (0.9, 0.9), (0.4, 0.1)], result="0-1") == "swindled"


def test_a_crazy_game_swings_both_ways():
    assert kind(LEVEL + [(0.5, 0.75), (0.5, 0.75), (0.5, 0.7), (0.7, 0.72)]) == "crazy"


def test_a_stomp_and_being_stomped():
    assert kind([(0.5, 0.6), (0.65, 0.85)] + [(0.9, 0.95)] * 6) == "stomp"
    assert kind([(0.5, 0.4), (0.35, 0.15)] + [(0.1, 0.05)] * 6, result="0-1") == "stomped"


def test_close_fights_and_grinds():
    assert kind(LEVEL + [(0.55, 0.6)], result="1/2-1/2") == "close"
    assert kind(LEVEL + [(0.5, 0.15)] + [(0.1, 0.05)] * 2, result="0-1") == "close"  # one move decided it
    assert kind([(0.5, 0.45), (0.4, 0.38), (0.35, 0.3), (0.28, 0.25), (0.22, 0.2), (0.18, 0.15), (0.12, 0.1)] + LEVEL[:0],
                result="0-1") == "grind"
