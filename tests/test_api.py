"""The hosted upload API (deploy/api/handler.py): sign-in sessions, per-player keys, uploads."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HANDLER = Path(__file__).resolve().parents[1] / "deploy" / "api" / "handler.py"


class NoSuchKey(Exception):
    pass


class FakeS3:
    exceptions = types.SimpleNamespace(NoSuchKey=NoSuchKey)

    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NoSuchKey(Key)
        body = self.objects[(Bucket, Key)]
        return {"Body": types.SimpleNamespace(read=lambda: body)}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NoSuchKey(Key)
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop((Bucket, o["Key"]), None)

    def get_paginator(self, name):
        objects = self.objects

        class Pages:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for b, k in list(objects) if b == Bucket and k.startswith(Prefix)]}]
        return Pages()

    def generate_presigned_url(self, op, ExpiresIn, Params):
        return f"https://s3.example/{Params['Key']}"


class Recorder:
    def __init__(self):
        self.sent, self.desired = [], 0

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append(json.loads(MessageBody))

    def describe_auto_scaling_groups(self, AutoScalingGroupNames):
        return {"AutoScalingGroups": [{"DesiredCapacity": self.desired}]}

    def set_desired_capacity(self, AutoScalingGroupName, DesiredCapacity, HonorCooldown):
        self.desired = DesiredCapacity


@pytest.fixture
def api(monkeypatch):
    s3, other = FakeS3(), Recorder()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name: s3 if name == "s3" else other
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    for k, v in {"TC_DATA_BUCKET": "data", "TC_SITE_BUCKET": "site", "TC_QUEUE_URL": "q", "TC_WORKER_GROUP": "g",
                 "TC_UPLOAD_KEY": "owner-secret-key-0123456789", "TC_OWNER": "LucidChess",
                 "TC_DOMAIN": "thoughtcoach.org"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("tc_api_handler", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.fake_s3, mod.fake_other = s3, other
    # lichess: game AbCd1234 is over, between lucidchess and someone
    monkeypatch.setattr(mod, "game_on_lichess", lambda gid: {"over": True, "players": {"lucidchess", "someone"}})
    return mod


def event(method, path, body=None, key=None, cookies=None, query=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body) if body else "",
            "headers": {"X-Thought-Coach-Key": key} if key else {}, "cookies": cookies or [],
            "queryStringParameters": query}


RECORDING = {"format": "thoughtcoach-arrows/2", "game_id": "AbCd1234", "events": []}


def test_session_cookie_round_trip_and_tampering(api):
    token = api.make_session("lucidchess", now=1000)
    assert api.read_session(token, now=2000) == "lucidchess"
    assert api.read_session(token.replace("lucidchess", "someone"), now=2000) is None
    assert api.read_session(token, now=1000 + 31 * 86400) is None  # expired
    assert api.read_session("garbage", now=2000) is None


def test_login_redirects_to_lichess_with_pkce(api):
    r = api.handler(event("GET", "/api/login"), None)
    assert r["statusCode"] == 302 and r["headers"]["Location"].startswith("https://lichess.org/oauth?")
    assert "code_challenge_method=S256" in r["headers"]["Location"]
    assert "redirect_uri=https%3A%2F%2Fthoughtcoach.org%2Fapi%2Fcallback" in r["headers"]["Location"]
    assert r["cookies"][0].startswith("tc_oauth=") and "HttpOnly" in r["cookies"][0]


def test_callback_rejects_wrong_state(api):
    r = api.handler(event("GET", "/api/callback", cookies=["tc_oauth=abc.verifier"], query={"code": "x", "state": "zzz"}), None)
    assert r["headers"]["Location"] == "/start?signin=failed"


def test_callback_creates_player_and_me_returns_key(api, monkeypatch):
    calls = []

    def fake_lichess(path, token=None, data=None, method=None):
        calls.append((path, method))
        return {"access_token": "t"} if path == "/api/token" and method is None else {"id": "newplayer", "username": "NewPlayer"}

    monkeypatch.setattr(api, "_lichess", fake_lichess)
    r = api.handler(event("GET", "/api/callback", cookies=["tc_oauth=st.verifier"], query={"code": "c", "state": "st"}), None)
    assert r["headers"]["Location"] == "/start"
    assert ("/api/token", "DELETE") in calls  # the lichess token is given back
    session = next(c for c in r["cookies"] if c.startswith("tc_session="))
    me = json.loads(api.handler(event("GET", "/api/me", cookies=[session.split(";")[0]]), None)["body"])
    assert me["signed_in"] and me["id"] == "newplayer" and me["home"] == "/u/newplayer/" and len(me["key"]) > 20
    assert ("site", "u/newplayer/index.html") in api.fake_s3.objects  # their games page exists at once
    assert json.loads(api.handler(event("GET", "/api/me"), None)["body"]) == {"signed_in": False}


def test_upload_needs_a_key_and_queues_for_that_player(api):
    assert api.handler(event("POST", "/api/recordings", RECORDING), None)["statusCode"] == 401
    r = api.handler(event("POST", "/api/recordings", RECORDING, key="owner-secret-key-0123456789"), None)
    body = json.loads(r["body"])
    assert r["statusCode"] == 202 and body["story_url"] == "/u/lucidchess/games/AbCd1234/story"
    assert api.fake_other.sent == [{"game_id": "AbCd1234", "user": "lucidchess"}]
    assert ("data", "recordings/lucidchess/AbCd1234.json") in api.fake_s3.objects
    assert api.fake_other.desired == 1


def test_only_players_can_send_a_game(api):
    user = api.get_or_create_user("stranger", "Stranger")
    r = api.handler(event("POST", "/api/recordings", RECORDING, key=user["key"]), None)
    assert r["statusCode"] == 403


def test_daily_limit(api, monkeypatch):
    monkeypatch.setattr(api, "MAX_PER_DAY", 1)
    key = "owner-secret-key-0123456789"
    assert api.handler(event("POST", "/api/recordings", RECORDING, key=key), None)["statusCode"] == 202
    richer = {**RECORDING, "events": [{"type": "shape_add"}]}
    assert api.handler(event("POST", "/api/recordings", richer, key=key), None)["statusCode"] == 429


def test_delete_account_removes_everything_for_that_player(api):
    user = api.get_or_create_user("lucidchess", "LucidChess")
    api.handler(event("POST", "/api/recordings", RECORDING, key=user["key"]), None)
    api.fake_s3.put_object(Bucket="site", Key="u/lucidchess/games/AbCd1234/index.html", Body=b"x")
    api.fake_s3.put_object(Bucket="site", Key="u/someone/index.html", Body=b"x")
    cookie = "tc_session=" + api.make_session("lucidchess")
    assert api.handler(event("POST", "/api/account/delete"), None)["statusCode"] == 401
    r = api.handler(event("POST", "/api/account/delete", cookies=[cookie]), None)
    assert r["statusCode"] == 200
    left = {k for _, k in api.fake_s3.objects}
    assert left == {"u/someone/index.html"}
