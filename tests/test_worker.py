"""The hosted worker's live progress (what the live page reads while a review runs)."""

import json

from thoughtcoach import progress
from thoughtcoach.worker import Live


class S3:
    def __init__(self):
        self.objects = {("site", "status/me/AbCd1234.json"): json.dumps({"facts": {"opp": "X"}, "state": "queued"}).encode()}
        self.writes = 0

    def get_object(self, Bucket, Key):
        body = self.objects[(Bucket, Key)]
        return {"Body": type("B", (), {"read": lambda self: body})()}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[(Bucket, Key)] = Body
        self.writes += 1


def test_live_keeps_the_facts_and_collects_what_each_stage_finds():
    s3 = S3()
    live = Live(s3, "site", "me", "AbCd1234")
    progress.HOOK = live.update
    try:
        progress.report("engine", done=3, total=40, move={"ply": 3, "n": 2, "san": "e4", "wp": 0.5})
        progress.report("engine", done=5, total=40, move={"ply": 5, "n": 3, "san": "d4", "wp": 0.48})
        progress.report("voice", said=12, quotes=[{"s": 30, "text": "c5 is kind of weak"}])
    finally:
        progress.HOOK = None
    status = json.loads(s3.objects[("site", "status/me/AbCd1234.json")])
    assert status["facts"] == {"opp": "X"} and status["state"] == "running" and status["stage"] == "voice"
    assert [m["san"] for m in status["moves"]] == ["e4", "d4"] and status["engine"] == [5, 40]
    assert status["found"]["said"] == 12


def test_progress_without_a_hook_does_nothing():
    progress.report("engine", done=1, total=2)  # no error
