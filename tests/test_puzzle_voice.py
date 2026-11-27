"""A puzzle solved out loud: speech resolved to moves on the puzzle's board (thoughtcoach/puzzle_voice.py)."""

from thoughtcoach.puzzle_voice import thoughts
from thoughtcoach.voice import voice_events

FEN = "4r1k1/ppq2pp1/3b4/6P1/3B1p1Q/2PP3P/PP2r3/1K3RR1 b - - 0 26"  # black to play; Be5 is best


def said(*sentences):
    segs, t = [], 0
    for s in sentences:
        words = [{"t": t + 300 * i, "end_t": t + 300 * i + 250, "word": w} for i, w in enumerate(s.split())]
        segs.append({"t": t, "end_t": t + 300 * len(words), "text": s, "words": words})
        t += 300 * len(words) + 2000
    return {"segments": segs, "words": [w for s in segs for w in s["words"]]}


def test_spoken_moves_become_candidates_and_their_ideas():
    events = voice_events(said("I could play bishop e5.", "Or rook takes b2 check.", "He wants pawn g6 here."), None)
    out = thoughts(FEN, events, "e2b2")
    assert [c["san"] for c in out["candidates"]] == ["Be5", "Rxb2+"]
    assert [c["uci"] for c in out["candidates"]] == ["d6e5", "e2b2"]
    assert [i["san"] for i in out["ideas"]] == ["g6"] and out["ideas"][0]["uci"] == "g5g6"


def test_nothing_said_about_moves():
    out = thoughts(FEN, voice_events(said("This is a tough one, let me think."), None))
    assert out == {"candidates": [], "ideas": [], "squares": []}


class _S3:
    def __init__(self, objs):
        self.objs = dict(objs)

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **_):
        self.objs[Key] = Body

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)


def test_worker_keeps_the_words_and_deletes_the_audio(monkeypatch):
    import json
    from thoughtcoach import puzzle_voice, worker
    s3 = _S3({"puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3d.json": json.dumps(
        {"id": "Abcd1234-52-0a1b2c3d", "puzzle": "Abcd1234-52", "fen": FEN, "uci": "d6e5", "audio": [{"data_base64": "x"}]}).encode()})
    monkeypatch.setattr(puzzle_voice, "process", lambda job: {"transcript": "bishop e5", "candidates": [{"san": "Be5"}]})
    worker.puzzle_voice_one(s3, "data", "lucidchess", "Abcd1234-52-0a1b2c3d")
    assert "puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3d.json" not in s3.objs  # the audio is gone
    result = json.loads(s3.objs["puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3d.result.json"])
    assert result["state"] == "done" and result["candidates"] == [{"san": "Be5"}] and result["puzzle"] == "Abcd1234-52"
    # a failure still deletes the audio, and says so
    s3.objs["puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3e.json"] = s3.objs.get("x", b'{"id": "y", "fen": "bad", "audio": []}')
    monkeypatch.setattr(puzzle_voice, "process", lambda job: 1 / 0)
    worker.puzzle_voice_one(s3, "data", "lucidchess", "Abcd1234-52-0a1b2c3e")
    assert "puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3e.json" not in s3.objs
    assert json.loads(s3.objs["puzzles/voice/lucidchess/Abcd1234-52-0a1b2c3e.result.json"])["state"] == "error"
