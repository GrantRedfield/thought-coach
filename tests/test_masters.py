"""Your habits next to masters' (masters.py), and the masters card in the story."""

from thoughtcoach import masters, story

BASE = {"q3": {"rate": 0.15}, "trust": {"rate": 0.06}, "search_dropped": {"rate": 0.08},
        "search_cands": {"rate": 2.5}, "search_many": {"rate": 0.04}}


def with_base(monkeypatch, base=BASE):
    masters.baseline.cache_clear()
    monkeypatch.setattr(masters, "baseline", lambda: base)


def test_gaps_are_ratios_in_the_bad_direction(monkeypatch):
    with_base(monkeypatch)
    g = masters.gaps({"q3": (15, 100), "trust": (18, 100), "q1": (5, 100)})
    assert round(g["q3"], 2) == 1.0  # level
    assert round(g["trust"], 2) == 3.0  # drops good moves 3x as often (lower is better)
    assert "q1" not in g  # no master rate for it here


def test_no_baseline_means_no_card(monkeypatch):
    with_base(monkeypatch, {})
    assert masters.card({"q3": (1, 10)}, {}) == ""


def test_search_counts_a_found_and_dropped_move():
    moves = [{"move_san": "Nc7", "best_move_san": "Bd7", "wp_loss": 0.3,
              "drawn_candidates": [{"move_san": "Bd7"}, {"move_san": "Nc7"}]},
             {"move_san": "e5", "best_move_san": "e5", "wp_loss": 0.0,
              "drawn_candidates": [{"move_san": "e5"}, {}]}]
    s = masters.search(moves)
    assert s["found"] == 2 and s["dropped"] == 0.5 and s["cands"] == 2


def test_masters_card_follows_its_moment_and_is_extra():
    beats = [{"kind": "turning", "ply": 28, "score": 1.3, "title": "t"},
             {"kind": "insight", "ply": 24, "score": 1.0, "title": "i"},
             {"kind": "masters", "ply": 28, "score": 1.1, "title": "m"}]
    chosen = story.pick(beats, 2)
    assert [b["kind"] for b in chosen] == ["insight", "turning", "masters"]
    alone = story.pick([b for b in beats if b["kind"] != "turning"], 2)
    assert [b["kind"] for b in alone] == ["insight"]  # no card about a moment that isn't shown
