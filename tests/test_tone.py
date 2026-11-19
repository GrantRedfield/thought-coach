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


def test_hybrid_asks_the_model_only_about_mood_sentences(monkeypatch):
    from thoughtcoach import tone
    asked = []
    monkeypatch.setattr(tone, "llm_read", lambda prev, text: asked.append(text) or {"mood": "upbeat", "gave_up": False})
    assert tone.mood("Knight again,")[0] == 0 and asked == []          # plain move talk: no model, neutral
    assert tone.mood("the king is more exposed than mine")[0] == 1     # needs context: the model reads it
    assert tone.mood("this is not good")[0] == 1 and len(asked) == 2  # a mood word: the model decides which way



def test_analyse_runs_on_a_real_recording():
    """End to end on a saved game with voice (skipped where the local games aren't present)."""
    import json
    from pathlib import Path

    import chess
    import chess.pgn
    import pytest

    from thoughtcoach import tone
    from thoughtcoach.voice import utterances

    gid = "WTQkEYK9"
    paths = [Path(f"out/{gid}.gaps.json"), Path(f"recordings/{gid}.json"), Path(f"out/{gid}.transcript.json"),
             Path(f"games/{gid}.pgn")]
    if not all(p.exists() for p in paths):
        pytest.skip("local game files not present")
    report = json.loads(paths[0].read_text(encoding="utf-8"))
    game = chess.pgn.read_game(open(paths[3], encoding="utf-8"))
    me = chess.WHITE if report["game"]["my_color"] == "white" else chess.BLACK
    out = tone.analyse(game, me, report, json.loads(paths[1].read_text(encoding="utf-8")),
                       json.loads(paths[2].read_text(encoding="utf-8")), utterances)
    assert out["moves"] and {r["mood"] for r in out["moves"]} <= {"upbeat", "worried", "neutral", "quiet"}
