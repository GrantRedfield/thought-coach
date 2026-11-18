"""Critical moments: which positions are picked, and that the page draws them."""

import html

from test_gaps import T, record, shape, win

from thoughtcoach.critical import pick, render_html
from thoughtcoach.gaps import compare_move

GAME = {"id": "abcdefgh", "white": "me", "black": "you", "result": "0-1", "my_color": "white"}


def test_costly_moves_first_then_sharp_positions():
    costly = record(move_uci="d2d3", move_san="d3", wp_after=0.40)
    sharp = {**record(), "ply": 7, "move_number": 4,
             "candidates": [{"move_san": "Bb5", "move_uci": "f1b5", "wp": 0.56},
                            {"move_san": "Bc4", "move_uci": "f1c4", "wp": 0.40}]}
    calm = {**record(), "ply": 9, "move_number": 5}
    moves = [compare_move(costly, win(shape("candidate", "f1", "b5", "Bb5")), T, evaluate=lambda f, u: 0.40),
             {**compare_move(sharp, None, T), "ply": 7}, {**compare_move(calm, None, T), "ply": 9}]
    report = {"game": GAME, "moves": moves, "summary": {"wp_lost_total": 0.16}}
    analysis = {"moves": [costly, sharp, calm]}
    assert [(c["move"]["ply"], c["why"]) for c in pick(report, analysis, 3)] == [(5, "costly"), (7, "sharp")]

    windows = {"windows": [{**win(shape("candidate", "f1", "b5", "Bb5")), "fen": costly["fen"]}]}
    page = render_html(report, analysis, windows)
    assert page.count("class='player'") == 2 and "only one good move" in page
    text = html.unescape(page)  # captions live escaped in the player data
    assert "You considered <b>Bb5</b>" in text and "You played <b>d3</b>" in text


def test_dashboard_renders_everything_on_one_page():
    from thoughtcoach.dashboard import render, takeaways
    costly = record(move_uci="d2d3", move_san="d3", wp_after=0.40)
    m = compare_move(costly, win(shape("candidate", "f1", "b5", "Bb5")), T, evaluate=lambda f, u: 0.40)
    from thoughtcoach.gaps import GAPS
    by_gap = {g: {"count": 0, "wp_lost": 0.0} for g in GAPS}
    by_gap["wrongly_rejected"] = {"count": 1, "wp_lost": 0.16}
    summary = {"wp_lost_total": 0.16, "by_gap": by_gap, "q3_real_threats": 0, "q3_spotted": 0, "q3_handled": 0,
               "q4_best_considered": 1, "q4_positions": 1, "moves_reviewed": 1, "moves_with_thoughts": 1}
    report = {"game": {**GAME, "time_control": "300+0"}, "moves": [m], "summary": summary,
              "thresholds": {"time_trouble": 30}}
    assert "3. d3 instead of Bb5" in takeaways(report, ".")[0]
    page = render(report, {"moves": [costly]}, {"windows": [{**win(), "fen": costly["fen"]}]})
    assert "class='timeline'" in page and "Where the WP went" in page and page.count("class='tab'") == 1
