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

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        import datetime
        keys = [k for b, k in self.objects if b == Bucket and k.startswith(Prefix)][:MaxKeys]
        now = datetime.datetime.now()
        return {"KeyCount": len(keys), "Contents": [{"Key": k, "LastModified": now} for k in keys]}

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
    tags = {"White": "LucidChess", "Black": "someone", "WhiteElo": "2320", "BlackElo": "2326", "Result": "0-1",
            "Opening": "English Opening", "TimeControl": "600+0", "WhiteRatingDiff": "-6"}
    monkeypatch.setattr(mod, "game_on_lichess", lambda gid: {"over": True, "players": {"lucidchess", "someone"}, "tags": tags})
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
    assert api.handler(event("POST", "/api/recordings", richer, key=key), None)["statusCode"] == 202  # the owner: no limit
    monkeypatch.setattr(api, "OWNER", "someone-else")  # a player who isn't running the site
    mine = api.get_or_create_user("lucidchess", "LucidChess")["key"]
    richest = {**RECORDING, "events": [{"type": "shape_add"}] * 2}
    assert api.handler(event("POST", "/api/recordings", richer, key=mine), None)["statusCode"] == 202
    assert api.handler(event("POST", "/api/recordings", richest, key=mine), None)["statusCode"] == 429


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


def test_returning_player_goes_to_their_games(api, monkeypatch):
    monkeypatch.setattr(api, "_lichess", lambda path, token=None, data=None, method=None:
                        {"access_token": "t"} if path == "/api/token" else {"id": "lucidchess", "username": "LucidChess"})
    api.fake_s3.put_object(Bucket="site", Key="status/lucidchess/AbCd1234.json", Body=b"{}")
    r = api.handler(event("GET", "/api/callback", cookies=["tc_oauth=st.v"], query={"code": "c", "state": "st"}), None)
    assert r["headers"]["Location"] == "/u/lucidchess/"


def test_queued_status_has_game_facts_and_shows_as_active(api):
    r = api.handler(event("POST", "/api/recordings", RECORDING, key="owner-secret-key-0123456789"), None)
    assert json.loads(r["body"])["live_url"] == "/live?u=lucidchess&g=AbCd1234"
    status = json.loads(api.fake_s3.objects[("site", "status/lucidchess/AbCd1234.json")])
    assert status["facts"]["opp"] == "someone" and status["facts"]["color"] == "white" and status["facts"]["rating_diff"] == "-6"
    assert status["cold_start"] is True and status["boot_s"] == 160
    mine = [f"tc_session={api.make_session('lucidchess')}"]
    active = json.loads(api.handler(event("GET", "/api/u/lucidchess/active", cookies=mine), None)["body"])["active"]
    assert active[0]["game_id"] == "AbCd1234" and active[0]["facts"]["opening"] == "English Opening"
    # someone else sees nothing about a private player's games in progress
    assert json.loads(api.handler(event("GET", "/api/u/lucidchess/active"), None)["body"])["active"] == []


def test_feedback_from_a_story_card(api):
    api.fake_s3.put_object(Bucket="site", Key="u/lucidchess/games/AbCd1234/story/index.html", Body=b"x")
    body = {"owner": "lucidchess", "game_id": "AbCd1234", "reason": "wrong", "comment": "I said I'm NOT lost",
            "card": {"index": 3, "kind": "gave_up", "ply": 40, "title": "You called it lost too early", "text": "..."}}
    r = api.handler(event("POST", "/api/feedback", body), None)
    assert r["statusCode"] == 201
    saved = [k for _, k in api.fake_s3.objects if k.startswith("feedback/")]
    assert len(saved) == 1 and "lucidchess-AbCd1234" in saved[0]
    assert api.handler(event("POST", "/api/feedback", {**body, "game_id": "ZZZZ9999"}), None)["statusCode"] == 404
    assert api.handler(event("POST", "/api/feedback", {**body, "reason": "spam"}), None)["statusCode"] == 400


def test_watchdog_with_nothing_waiting(api, monkeypatch):
    class Q:
        def get_queue_attributes(self, **kw):
            return {"Attributes": {"ApproximateNumberOfMessages": "0"}}
    monkeypatch.setattr(api, "sqs", Q())
    assert api.handler({"tc": "watchdog"}, None) == {"waiting": 0}


def test_watchdog_wakes_a_worker_when_reviews_wait(api, monkeypatch):
    class Q:
        def get_queue_attributes(self, **kw):
            return {"Attributes": {"ApproximateNumberOfMessages": "2"}}
    monkeypatch.setattr(api, "sqs", Q())
    monkeypatch.setattr(api, "wake_worker", lambda: 60)
    monkeypatch.setattr(api, "grow_burst", lambda: None)
    assert api.handler({"tc": "watchdog"}, None) == {"waiting": 2, "woke": True}


def test_admin_is_for_the_owner_only(api, monkeypatch):
    assert api.handler(event("GET", "/api/admin"), None)["statusCode"] == 404  # not signed in
    stranger = "tc_session=" + api.make_session("someone")
    assert api.handler(event("GET", "/api/admin", cookies=[stranger]), None)["statusCode"] == 404
    class Q:
        def get_queue_attributes(self, **kw):
            return {"Attributes": {"ApproximateNumberOfMessages": "0", "ApproximateNumberOfMessagesNotVisible": "0"}}
    class G:
        def describe_auto_scaling_groups(self, **kw):
            return {"AutoScalingGroups": [{"Instances": [], "DesiredCapacity": 0, "MaxSize": 1}]}
    monkeypatch.setattr(api, "sqs", Q())
    monkeypatch.setattr(api, "asg", G())
    api.get_or_create_user("lucidchess", "LucidChess")
    api.fake_s3.put_object(Bucket="site", Key="status/lucidchess/AbCd1234.json",
                           Body=json.dumps({"state": "error", "error": "boom", "updated": 1}).encode())
    owner = "tc_session=" + api.make_session("lucidchess")
    r = api.handler(event("GET", "/api/admin", cookies=[owner]), None)
    assert r["statusCode"] == 200 and "AbCd1234" in r["body"] and "boom" in r["body"] and "LucidChess" in r["body"]


def test_titled_players_count_as_masters_unless_they_opt_out(api):
    assert not api.is_master(api.get_or_create_user("plain", "Plain"))
    assert not api.is_master(api.get_or_create_user("robot", "Robot", "BOT"))
    gm = api.get_or_create_user("bigshot", "BigShot", "GM")
    assert gm["title"] == "GM" and api.is_master(gm)
    cookie = "tc_session=" + api.make_session("bigshot")
    me = json.loads(api.handler(event("GET", "/api/me", cookies=[cookie]), None)["body"])
    assert me["title"] == "GM" and me["benchmark"] is True
    r = api.handler(event("POST", "/api/benchmark", {"on": False}, cookies=[cookie]), None)
    assert r["statusCode"] == 200 and json.loads(r["body"]) == {"benchmark": False}
    assert not api.is_master(api.get_or_create_user("bigshot", "BigShot", "GM"))  # signing in again keeps the choice
    assert api.handler(event("POST", "/api/benchmark", {"on": "yes"}, cookies=[cookie]), None)["statusCode"] == 400
    assert api.handler(event("POST", "/api/benchmark", {"on": True}), None)["statusCode"] == 401


def test_page_events_are_counted_by_name_only(api):
    assert api.handler({**event("POST", "/api/event"), "body": "sample_open"}, None)["statusCode"] == 204
    assert api.handler({**event("POST", "/api/event"), "body": "anything"}, None)["statusCode"] == 400
    [(_, key)] = [k for k in api.fake_s3.objects if k[1].startswith("events/")]
    assert json.loads(api.fake_s3.objects[("data", key)]) == {"sample_open": 1}


def test_general_feedback_needs_a_reason_and_words(api):
    ok = api.handler(event("POST", "/api/feedback", {"kind": "general", "reason": "bug", "comment": "The setup page froze"}), None)
    assert ok["statusCode"] == 201
    assert api.handler(event("POST", "/api/feedback", {"kind": "general", "reason": "spam", "comment": "hello"}), None)["statusCode"] == 400
    assert api.handler(event("POST", "/api/feedback", {"kind": "general", "reason": "idea", "comment": ""}), None)["statusCode"] == 400
    [(_, key)] = [k for k in api.fake_s3.objects if k[1].startswith("feedback/")]
    rec = json.loads(api.fake_s3.objects[("data", key)])
    assert rec["reason"] == "bug" and rec["by"] == "anonymous"


# ---------------------------------------------------------------- private reviews and share links

def _page(api, path, cookies=None, query=None):
    return api.handler(event("GET", path, cookies=cookies, query=query), None)


def _seed(api):
    api.fake_s3.put_object("site", "u/lucidchess/index.html", "<p>home</p>")
    api.fake_s3.put_object("site", "u/lucidchess/games/AbCd1234/index.html", "<p>review</p>")
    api.fake_s3.put_object("site", "u/lucidchess/games/ZzZz9999/index.html", "<p>other</p>")
    api.fake_s3.put_object("site", "status/lucidchess/AbCd1234.json", json.dumps({"game_id": "AbCd1234", "state": "done", "found": {"moves": 40}}))
    api.fake_s3.put_object("data", "users/lucidchess.json", json.dumps({"id": "lucidchess", "name": "LucidChess", "key": "k"}))


def test_reviews_are_private_by_default(api):
    _seed(api)
    r = _page(api, "/u/lucidchess/games/AbCd1234/")
    assert r["statusCode"] == 403 and "This review is private" in r["body"] and "review" not in r["body"].split("<h1>")[0][-5:]
    assert _page(api, "/u/lucidchess/")["statusCode"] == 403
    assert _page(api, "/status/lucidchess/AbCd1234.json")["statusCode"] == 403
    # the extension still learns the state, nothing more
    s = json.loads(api.handler(event("GET", "/api/u/lucidchess/games/AbCd1234"), None)["body"])
    assert s == {"game_id": "AbCd1234", "state": "done"}


def test_the_owner_sees_their_reviews_and_others_do_not(api):
    _seed(api)
    mine = [f"tc_session={api.make_session('lucidchess')}"]
    r = _page(api, "/u/lucidchess/games/AbCd1234", cookies=mine)  # (no trailing slash: index.html)
    assert r["statusCode"] == 200 and r["body"] == "<p>review</p>" and r["headers"]["Cache-Control"] == "private, no-store"
    other = [f"tc_session={api.make_session('someone')}"]
    assert _page(api, "/u/lucidchess/games/AbCd1234/", cookies=other)["statusCode"] == 403
    assert _page(api, "/u/lucidchess/../someone/", cookies=mine)["statusCode"] in (400, 403, 404)


def test_share_links_open_one_game_and_can_be_revoked(api):
    _seed(api)
    mine = [f"tc_session={api.make_session('lucidchess')}"]
    made = json.loads(api.handler(event("POST", "/api/share", body={"game_id": "AbCd1234"}, cookies=mine), None)["body"])
    token = made["token"]
    assert made["url"] == f"/u/lucidchess/games/AbCd1234/?s={token}"
    r = _page(api, "/u/lucidchess/games/AbCd1234/", query={"s": token})
    assert r["statusCode"] == 200 and r["cookies"][0].startswith(f"tc_s_lucidchess={token};")
    # the remembered link opens the game's story too, but not other games
    remembered = [f"tc_s_lucidchess={token}"]
    api.fake_s3.put_object("site", "u/lucidchess/games/AbCd1234/story/index.html", "<p>story</p>")
    assert _page(api, "/u/lucidchess/games/AbCd1234/story/", cookies=remembered)["statusCode"] == 200
    assert _page(api, "/u/lucidchess/games/ZzZz9999/", cookies=remembered)["statusCode"] == 403
    assert _page(api, "/u/lucidchess/games/AbCd1234/", query={"s": "guessed-token-guessed-token"})["statusCode"] == 403
    # revoked: gone at once, for the address and the remembered cookie alike
    api.handler(event("POST", "/api/share/revoke", body={"token": token}, cookies=mine), None)
    assert _page(api, "/u/lucidchess/games/AbCd1234/", query={"s": token})["statusCode"] == 403
    assert _page(api, "/u/lucidchess/games/AbCd1234/", cookies=remembered)["statusCode"] == 403


def test_making_reviews_public_and_private_again(api):
    _seed(api)
    mine = [f"tc_session={api.make_session('lucidchess')}"]
    assert api.handler(event("POST", "/api/sharing", body={"visibility": "public"}), None)["statusCode"] == 401  # signed in only
    api.handler(event("POST", "/api/sharing", body={"visibility": "public"}, cookies=mine), None)
    assert _page(api, "/u/lucidchess/")["statusCode"] == 200
    info = json.loads(api.handler(event("GET", "/api/sharing", cookies=mine), None)["body"])
    assert info["visibility"] == "public"
    api.handler(event("POST", "/api/sharing", body={"visibility": "private"}, cookies=mine), None)
    assert _page(api, "/u/lucidchess/")["statusCode"] == 403
