from thoughtcoach import story

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def move(n, san, loss, **extra):
    return {"ply": 2 * n - 1, "move_number": n, "move_san": san, "fen": FEN, "skipped": None, "clock_before": 200,
            "wp_before": 0.5, "wp_after": 0.5 - loss, "wp_loss": loss, "mistake": loss >= 0.05,
            "best_move_san": "Bb5", "drawn_candidates": [], "q1": {"weaknesses": []}, "q3": {}, "q4": {}, **extra}


def report(moves, by_gap):
    return {"game": {"id": "game0001", "white": "me", "black": "them", "my_color": "white", "result": "0-1",
                     "time_control": "600+0"},
            "thresholds": {"time_trouble": 30},
            "summary": {"moves_with_thoughts": 2, "by_gap": by_gap, "q1_list": [], "q2_list": []},
            "moves": moves, "insights": []}


def test_story_keeps_the_standout_moments_and_picks_a_focus():
    moves = [move(3, "Bc4", 0.30, drawn_candidates=[{"move_san": "Bc4", "legal": True, "played": True}]),
             move(4, "d3", 0.01, q3={"status": "not_drawn", "threat_san": "Nd4", "swing": 0.02})]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.30}, "opponent_idea": {"wp_lost": 0.0}})
    s = story.build(r, {"moves": []}, None, None)
    kinds = [b["kind"] for b in s["beats"]]
    assert kinds == ["turning"]  # the tiny threat (2%) doesn't make the cut
    assert "Bb5 never came up" in s["beats"][0]["body"]
    assert s["focus"]["key"] == "candidates" and s["focus"]["title"] == "Find a second candidate"
    page = story.render(s)
    assert page.count("class='slide'") == 3  # intro (with the result), the turning point, the focus
    assert "Your focus for the next game" in page and "https://lichess.org/game0001" in page


def test_history_can_tip_the_focus():
    moves = [move(3, "Bc4", 0.06)]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.03}, "opponent_idea": {"wp_lost": 0.02}})
    s = story.build(r, {"moves": []}, None, None, history={"share": {"q3": 0.6, "candidates": 0.0}, "note": {}})
    assert s["focus"]["key"] == "q3"


def test_a_game_decided_by_one_move_gets_a_short_story():
    moves = [move(3, "Bc4", 0.40), move(5, "d3", 0.06), move(6, "h3", 0.02, q3={"status": "spotted", "threat_san": "Nd4", "swing": 0.5})]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.46}})
    s = story.build(r, {"moves": []}, None, None)
    assert s["decided_by"] == "3. Bc4" and len(s["beats"]) <= 2
    assert "It came down to one move: 3. Bc4." in story.render(s)


def test_a_game_with_little_thinking_shown_asks_for_more_first():
    moves = [move(p, "a3", 0.0) for p in range(3, 27, 2)] + [move(27, "Bc4", 0.30)]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.30}})
    r["summary"]["moves_with_thoughts"] = 2
    s = story.build(r, {"moves": []}, None, None)
    assert s["focus"]["key"] == "input" and "Only 2 of your 13 moves" in s["focus"]["why"]
    assert "How to show your thinking" in story.render(s)
    r["summary"]["moves_with_thoughts"] = 6  # enough to judge: back to what cost the most
    assert story.build(r, {"moves": []}, None, None)["focus"]["key"] == "candidates"


def test_a_missed_worst_piece_move_makes_aagaard_q2_the_focus():
    m = move(21, "h3", 0.25, best_move_san="Rd1")
    m["gap"] = "missed_candidate"
    m["q2"] = {"worst": {"piece": "rook", "square": "f1", "engine_move": "Rd1", "you": None}}
    r = report([m], {"missed_candidate": {"wp_lost": 0.25}})
    f = story.build(r, {"moves": []}, None, None)["focus"]
    assert f["key"] == "q2" and "bringing your rook on f1 into the game" in f["why"]


def test_a_missed_weakness_makes_aagaard_q1_the_focus():
    m = move(21, "h3", 0.25, best_move_san="Nxe6")
    m["gap"] = "missed_candidate"
    m["q1"] = {"weaknesses": [{"square": "e6", "kind": "hole", "side": "theirs", "you": None}], "found": []}
    r = report([m], {"missed_candidate": {"wp_lost": 0.25}})
    f = story.build(r, {"moves": []}, None, None)["focus"]
    assert f["key"] == "q1" and "their hole on e6" in f["why"] and "aim a candidate" in f["habit"]


def test_a_rushed_critical_move_makes_time_the_focus():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    moves, records = [], []
    for n in range(3, 13):  # quiet moves: three good options each, ten seconds a move
        moves.append(move(n, "a3", 0.0, fen=fen))
        records.append({"ply": 2 * n - 1, "move_number": n, "move_san": "a3", "fen": fen, "time_spent": 10,
                        "wp_loss": 0.0, "clock_before": 300, "best_move_san": "a3", "opponent_move_san": "Nc6",
                        "candidates": [{"move_uci": "a2a3"}, {"move_uci": "h2h3"}, {"move_uci": "d2d3"}]})
    records[2]["time_spent"] = 70  # a long think where anything was fine
    m = move(13, "Bc4", 0.40, fen=fen, gap="missed_candidate")
    moves.append(m)
    records.append({"ply": 25, "move_number": 13, "move_san": "Bc4", "fen": fen, "time_spent": 4, "wp_loss": 0.40,
                    "clock_before": 300, "best_move_san": "Bb5", "opponent_move_san": "Nc6",
                    "candidates": [{"move_uci": "f1b5"}]})
    r = report(moves, {"missed_candidate": {"wp_lost": 0.40}})
    r["summary"]["moves_with_thoughts"] = 11
    s = story.build(r, {"moves": records}, None, None)
    assert s["focus"]["key"] == "sharp"
    assert "only Bb5 held, and you took 4 s" in s["focus"]["why"] and "70 s on 5. a3" in s["focus"]["why"]
    turning = next(b for b in s["beats"] if b["kind"] == "turning")
    assert "Only Bb5 held, and you took 4 s" in turning["body"]


def test_ahead_reply_and_caught():
    from thoughtcoach import ahead
    assert ahead._reply("27...Nxc3+ 28. Qxc3 Qxc3") == "Qxc3"
    assert ahead._reply("18. e4 a5 19. Nd2") == "a5"
    rec = {"ply": 54, "move_number": 27, "move_san": "Nxc3+", "wp_before": 0.8, "wp_after": 0.16, "wp_loss": 0.64,
           "time_spent": 12, "clock_before": 200, "played_line_san": "27...Nxc3+ 28. Qxc3"}
    [m] = ahead.caught({"moves": [rec]}, {"thresholds": {"time_trouble": 30}})
    assert m["reply"] == "Qxc3" and m["tactic"] and m["zone"] == "ahead"



def test_moves_in_a_card_become_buttons():
    st = {"color": "black", "plies": [{"fen": "", "label": "the start", "uci": ""}] + [
        {"fen": "", "label": f"x {i}", "uci": ""} for i in range(1, 60)]}
    st["plies"][54] = {"fen": "", "label": "27... Nxc3+", "uci": "d5c3"}
    html = story._link_moves("At 27... Nxc3+ only Nb6 held; around move 11 you said", st)
    assert "data-ply='54'>27... Nxc3+</button>" in html
    assert "data-ply='21'>move 11</button>" in html  # black: your move 11 is ply 22, the position before it 21
    assert story._link_moves("At 27... Qd2 something", st) == "At 27... Qd2 something"  # not the game's move: left alone


def test_not_horrible_is_not_judging():
    from thoughtcoach.psych import judging
    assert judging("so stupid") and not judging("it's not horrible") and not judging("it doesn't look horrible")


def test_voice_off_is_mentioned_gently():
    moves = [move(3, "Bc4", 0.30, drawn_candidates=[{"move_san": "Bc4", "legal": True, "played": True}])]
    r = report(moves, {"missed_candidate": {"wp_lost": 0.30}})
    assert "Voice was off" not in story.render(story.build(r, {"moves": []}, None, None))
    r["voice"] = "off"
    page = story.render(story.build(r, {"moves": []}, None, None))
    assert page.count("Voice was off") == 1 and "Switch it on" in page
