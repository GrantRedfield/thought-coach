"""The home page: latest game up front, form, every game."""

from thoughtcoach import home


def _game(gid, played, result, color="white", elo="2300", per_move=0.03):
    moves = [{"ply": i, "move_san": "e4", "wp_after": 0.5 - i * 0.01, "move_number": i} for i in range(1, 30, 2)]
    report = {"game": {"id": gid, "date": played[:10], "white": "Me" if color == "white" else "Opp",
                       "black": "Opp" if color == "white" else "Me", "white_elo": elo, "black_elo": "2400",
                       "result": result, "time_control": "600+0", "my_color": color}}
    return {"id": gid, "report": report, "analysis": {"moves": moves}, "played": played}


def _prof(loaded):
    games = [{"id": g["id"], "per_move": 0.03, "costly": 2, "moves": 15, "won": False, "strike": None} for g in loaded]
    return {"games": games, "questions": {"q1": [0, 0], "q2": [0, 0], "q3": [3, 10], "q4": [4, 10]},
            "strikes": {"games": 0, "you": {"n": 0, "won": 0}}}


def test_latest_game_is_the_hero_with_its_story():
    loaded = [_game("AAAAAAAA", "2026.11.16 10:00:00", "1-0"), _game("BBBBBBBB", "2026.11.17 20:00:00", "0-1")]
    story = {"decided_by": "29. b5", "beats": [{"kind": "insight", "title": "x", "body": "other", "fen": None},
                                            {"kind": "turning", "title": "t", "body": "the moment", "eyebrow": "Turning point", "fen": None}],
             "focus": {"title": "Checks, captures and threats first", "habit": "Look at forcing moves"}, "arrows": 12, "said": 3}
    rows = home.facts(loaded, _prof(loaded))
    page = home.hero(rows[-1], story, (0.02, 0.5, 0.48))
    assert "You lost to" in page and "Opp" in page and "It came down to one move: 29. b5" in page
    assert "/games/BBBBBBBB/story" in page and "Checks, captures and threats first" in page
    assert "than usual (your average move: 50% → 48%)" in page and "the moment" in page and "other" not in page


def test_render_without_games_invites_a_first_game():
    page = home.render([], None, {}, {"CCCCCCCC": {"game_id": "CCCCCCCC", "state": "running"}})
    assert "Your first review is on its way" in page and "Being reviewed right now" in page


def test_form_shows_rating_trend_and_record():
    loaded = [_game(f"G{i:07d}", f"2026.11.1{i} 10:00:00", "1-0", elo=str(2300 + i * 10)) for i in range(3)]
    page = home.form(home.facts(loaded, _prof(loaded)), _prof(loaded))
    assert "2320" in page and "+20 since your first review" in page and "3 won" in page
