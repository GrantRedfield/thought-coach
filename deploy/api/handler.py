"""Thought Coach upload API (AWS Lambda behind an HTTP API).

Sign in (lichess OAuth with PKCE: no app registration, no lichess password ever reaches us; we
only ask lichess who you are, then give the token back):
    GET  /api/login                       -> lichess sign-in page
    GET  /api/callback                    <- lichess; sets the session cookie, -> /start
    GET  /api/me                          who is signed in, with their upload key (same-site only)
    GET  /api/logout
    POST /api/account/delete              delete everything stored for the signed-in player
Uploads (header X-Thought-Coach-Key = your upload key, which the extension gets from /start):
    POST /api/uploads                     {"game_id"}: a one-time signed S3 link to upload the recording to
    POST /api/recordings/<id>/uploaded    {"upload_id"}: the recording is in S3, review it
    POST /api/recordings                  a small recording in the request body (under ~6 MB)
    GET  /api/u/<user>/games/<id>         a review's status
    GET  /health
Recordings with voice are often bigger than a Lambda request can carry, so the extension uploads
them straight to S3 with the signed link (valid 15 minutes, for that one file).

Fair play: a recording is only accepted once lichess says the game is over, and only from one of
the two players. A poorer recording (fewer shapes, no audio) never replaces a richer one already
stored. Accepted games go on the review queue, and the worker is woken up if it is switched off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import boto3

GAME_ID = re.compile(r"^[A-Za-z0-9]{8}$")
USER_ID = re.compile(r"^[a-z0-9_-]{2,30}$")
FORMAT = "thoughtcoach-arrows/2"
LICHESS = "https://lichess.org"
SESSION_DAYS = 30
MAX_PER_DAY = 30  # reviews a player can ask for per day (each one starts a server)
s3, sqs, asg = boto3.client("s3"), boto3.client("sqs"), boto3.client("autoscaling")
DATA, SITE = os.environ["TC_DATA_BUCKET"], os.environ["TC_SITE_BUCKET"]
QUEUE, GROUP, KEY = os.environ["TC_QUEUE_URL"], os.environ["TC_WORKER_GROUP"], os.environ["TC_UPLOAD_KEY"]
OWNER = os.environ.get("TC_OWNER", "").lower()  # the old shared key uploads as this player
SECRET = hmac.new(KEY.encode(), b"thoughtcoach session cookie", hashlib.sha256).digest()
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type,x-thought-coach-key",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS"}


def reply(code: int, body: dict, cors: bool = True, cookies: list[str] | None = None) -> dict:
    out = {"statusCode": code, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store",
                                           **(CORS if cors else {})}, "body": json.dumps(body)}
    if cookies:
        out["cookies"] = cookies
    return out


def redirect(to: str, cookies: list[str] | None = None) -> dict:
    out = {"statusCode": 302, "headers": {"Location": to, "Cache-Control": "no-store"}, "body": ""}
    if cookies:
        out["cookies"] = cookies
    return out


# ---------------------------------------------------------------- storage helpers


def _get_json(bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def _put_json(bucket: str, key: str, body: dict, **extra) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(body).encode(), ContentType="application/json", **extra)


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------- players


def get_or_create_user(uid: str, name: str) -> dict:
    """users/<id>.json: the player's name and upload key; keys/<sha256>.json maps a key back."""
    user = _get_json(DATA, f"users/{uid}.json")
    if user:
        if user.get("name") != name:
            user["name"] = name
            _put_json(DATA, f"users/{uid}.json", user)
        return user
    user = {"id": uid, "name": name, "key": secrets.token_urlsafe(32), "created": time.time()}
    _put_json(DATA, f"users/{uid}.json", user)
    _put_json(DATA, f"keys/{_key_hash(user['key'])}.json", {"user": uid})
    # Their games page exists from the start, so the links on /start never lead nowhere.
    if not _exists(SITE, f"u/{uid}/index.html"):
        s3.put_object(Bucket=SITE, Key=f"u/{uid}/index.html", Body=empty_home(name).encode("utf-8"),
                      ContentType="text/html; charset=utf-8", CacheControl="no-cache")
    return user


def _exists(bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:  # noqa: BLE001 - missing (404) or not allowed: treat as absent
        return False


def empty_home(name: str) -> str:
    n = _esc(name)
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Thought Coach</title>
<link rel="stylesheet" href="/site.css"></head><body><main class="narrow">
<p class="brand"><a href="/">Thought Coach</a></p><h1>{n}'s games</h1>
<p class="lead">No reviews yet. Play a game on lichess with the extension on: the review shows up here
a few minutes after the game ends.</p>
<p><a class="btn primary" href="https://lichess.org/">Play on lichess</a> <a class="btn" href="/start">Setup</a>
<a class="btn" href="/api/logout">Sign out</a></p></main></body></html>"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def user_for_key(key: str) -> str | None:
    if not key:
        return None
    if OWNER and hmac.compare_digest(key, KEY):
        return OWNER
    found = _get_json(DATA, f"keys/{_key_hash(key)}.json")
    return found["user"] if found else None


# ---------------------------------------------------------------- sessions (signed cookie)


def _sign(value: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(SECRET, value.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_session(uid: str, now: float | None = None) -> str:
    value = f"{uid}.{int((now or time.time()) + SESSION_DAYS * 86400)}"
    return f"{value}.{_sign(value)}"


def read_session(token: str, now: float | None = None) -> str | None:
    try:
        uid, expires, sig = token.rsplit(".", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(f"{uid}.{expires}")) or not expires.isdigit():
        return None
    if int(expires) < (now or time.time()) or not USER_ID.match(uid):
        return None
    return uid


def cookies(event: dict) -> dict:
    jar = {}
    for c in event.get("cookies") or []:
        name, _, value = c.partition("=")
        jar[name.strip()] = value.strip()
    return jar


def _cookie(name: str, value: str, path: str, max_age: int) -> str:
    return f"{name}={value}; Path={path}; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


def signed_in(event: dict) -> str | None:
    return read_session(cookies(event).get("tc_session", ""))


# ---------------------------------------------------------------- lichess sign-in (OAuth, PKCE)


def _origin(event: dict) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    host = headers.get("x-forwarded-host") or headers.get("host", "")
    return f"https://{os.environ.get('TC_DOMAIN') or host}"


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def login(event: dict) -> dict:
    state, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(48)
    origin = _origin(event)
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": origin.split("//", 1)[1], "redirect_uri": f"{origin}/api/callback",
        "code_challenge_method": "S256", "code_challenge": challenge(verifier), "state": state})
    return redirect(f"{LICHESS}/oauth?{query}", [_cookie("tc_oauth", f"{state}.{verifier}", "/api", 600)])


def _lichess(path: str, token: str | None = None, data: dict | None = None, method: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "thoughtcoach"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(f"{LICHESS}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def callback(event: dict) -> dict:
    q = event.get("queryStringParameters") or {}
    state, _, verifier = cookies(event).get("tc_oauth", "").partition(".")
    clear = _cookie("tc_oauth", "", "/api", 0)
    if q.get("error"):
        return redirect("/start?signin=cancelled", [clear])
    if not state or not hmac.compare_digest(state, q.get("state", "")) or not q.get("code"):
        return redirect("/start?signin=failed", [clear])
    origin = _origin(event)
    try:
        token = _lichess("/api/token", data={
            "grant_type": "authorization_code", "code": q["code"], "code_verifier": verifier,
            "redirect_uri": f"{origin}/api/callback", "client_id": origin.split("//", 1)[1]})["access_token"]
        account = _lichess("/api/account", token=token)
        try:
            _lichess("/api/token", token=token, method="DELETE")  # we only needed to know who you are
        except (urllib.error.URLError, OSError):
            pass
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return redirect("/start?signin=failed", [clear])
    uid, name = str(account.get("id", "")).lower(), str(account.get("username", ""))
    if not USER_ID.match(uid):
        return redirect("/start?signin=failed", [clear])
    get_or_create_user(uid, name or uid)
    # Players with reviews go straight to their games (latest story up front); new ones to setup.
    has_games = s3.list_objects_v2(Bucket=SITE, Prefix=f"status/{uid}/", MaxKeys=1).get("KeyCount", 0) > 0
    return redirect(f"/u/{uid}/" if has_games else "/start",
                    [clear, _cookie("tc_session", make_session(uid), "/", SESSION_DAYS * 86400)])


def me(event: dict) -> dict:
    uid = signed_in(event)
    user = uid and _get_json(DATA, f"users/{uid}.json")
    if not user:
        return reply(200, {"signed_in": False}, cors=False)
    return reply(200, {"signed_in": True, "id": user["id"], "name": user["name"], "key": user["key"],
                       "home": f"/u/{user['id']}/"}, cors=False)


def logout(event: dict) -> dict:
    return redirect("/", [_cookie("tc_session", "", "/", 0)])


def _delete_prefix(bucket: str, prefix: str) -> None:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})


def delete_account(event: dict) -> dict:
    """Everything stored for the signed-in player: account, key, recordings, reviews, pages.
    (POST with the session cookie, which browsers only send from thoughtcoach.org itself.)"""
    uid = signed_in(event)
    if not uid:
        return reply(401, {"error": "not signed in"}, cors=False)
    user = _get_json(DATA, f"users/{uid}.json")
    if user:
        s3.delete_object(Bucket=DATA, Key=f"keys/{_key_hash(user['key'])}.json")
    for key in (f"users/{uid}.json", f"usage/{uid}.json"):
        s3.delete_object(Bucket=DATA, Key=key)
    for prefix in (f"recordings/{uid}/", f"data/{uid}/", f"incoming/{uid}/"):
        _delete_prefix(DATA, prefix)
    for prefix in (f"u/{uid}/", f"status/{uid}/"):
        _delete_prefix(SITE, prefix)
    return reply(200, {"deleted": True}, cors=False, cookies=[_cookie("tc_session", "", "/", 0)])


# ---------------------------------------------------------------- uploads


def richness(recording: dict) -> int:
    shapes = sum(1 for e in recording.get("events") or [] if e.get("type") == "shape_add")
    return shapes + 1000 * len(recording.get("audio") or [])


def game_on_lichess(gid: str) -> dict | None:
    """{"over": bool, "players": {white id, black id}} from lichess's public export; None if unreachable."""
    req = urllib.request.Request(f"{LICHESS}/game/export/{gid}?moves=false&tags=true",
                                 headers={"Accept": "application/x-chess-pgn", "User-Agent": "thoughtcoach"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            pgn = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return {"over": False, "players": set(), "missing": True} if err.code == 404 else None
    except OSError:
        return None
    m = re.search(r'\[Result "([^"]+)"\]', pgn)
    abandoned = re.search(r'\[Termination "(Abandoned|Aborted)"\]', pgn)
    if abandoned and (not m or m.group(1) == "*"):
        raise ValueError("lichess says this game was abandoned: there is nothing to review")
    players = {p.lower() for p in re.findall(r'\[(?:White|Black) "([^"]+)"\]', pgn)}
    tags = dict(re.findall(r'\[(\w+) "([^"]*)"\]', pgn))
    return {"over": bool(m) and m.group(1) != "*", "players": players, "tags": tags}


def game_facts(tags: dict, user: str) -> dict:
    """Who played whom, for the live page while the review runs."""
    white = tags.get("White", "").lower() == user
    me, opp = ("White", "Black") if white else ("Black", "White")
    return {"color": "white" if white else "black", "me": tags.get(me), "opp": tags.get(opp),
            "my_elo": tags.get(f"{me}Elo"), "opp_elo": tags.get(f"{opp}Elo"), "result": tags.get("Result"),
            "opening": tags.get("Opening"), "tc": tags.get("TimeControl"), "date": tags.get("UTCDate"),
            "rating_diff": tags.get(f"{me}RatingDiff")}


def status(user: str, gid: str) -> dict | None:
    return _get_json(SITE, f"status/{user}/{gid}.json")


def active(user: str) -> list[dict]:
    """This player's reviews queued or running now (the newest few statuses)."""
    objs = s3.list_objects_v2(Bucket=SITE, Prefix=f"status/{user}/").get("Contents", [])
    found = []
    for o in sorted(objs, key=lambda o: o["LastModified"], reverse=True)[:5]:
        s = _get_json(SITE, o["Key"]) or {}
        if s.get("state") in ("queued", "running") and time.time() - (s.get("updated") or s.get("queued_at") or 0) < 3600:
            found.append({"game_id": s["game_id"], "state": s["state"], "facts": s.get("facts") or {}})
    return found


BOOT_WARM_S, BOOT_COLD_S = 60, 160  # starting the stopped server from the warm pool / building a new one


def wake_worker() -> int:
    """Start the worker if it is switched off. Returns the expected seconds until it is working
    (0 if it is already running), for the live page's time estimate."""
    group = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP])["AutoScalingGroups"][0]
    if group["DesiredCapacity"] > 0:
        return 0
    try:
        pool = asg.describe_warm_pool(AutoScalingGroupName=GROUP).get("Instances", [])
        warm = any(i.get("LifecycleState") == "Warmed:Stopped" for i in pool)
    except Exception:  # noqa: BLE001 - no warm pool: a new server
        warm = False
    asg.set_desired_capacity(AutoScalingGroupName=GROUP, DesiredCapacity=1, HonorCooldown=False)
    return BOOT_WARM_S if warm else BOOT_COLD_S


def _body(event: dict) -> str:
    raw = event.get("body") or ""
    return base64.b64decode(raw).decode("utf-8") if event.get("isBase64Encoded") else raw


def uploader(event: dict) -> str | None:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return user_for_key(headers.get("x-thought-coach-key", ""))


def _check_game(gid: str, user: str, facts: dict | None = None) -> dict | None:
    """None if lichess says the game is over and this player played it; otherwise the reply to send.
    `facts` (if given) is filled with the game's facts."""
    try:
        game = game_on_lichess(gid)
    except ValueError as err:
        return reply(400, {"error": str(err)})
    if game is None:
        return reply(502, {"error": "could not check the game on lichess"})
    if game.get("missing"):
        return reply(400, {"error": "lichess has no game with that id"})
    if not game["over"]:
        return reply(409, {"error": "lichess says this game is still in progress; try again when it is over"})
    if user not in game["players"]:
        return reply(403, {"error": "you can only send your own games (you are not a player in this one)"})
    if facts is not None:
        facts.update(game_facts(game.get("tags") or {}, user))
    return None


def signed_upload(event: dict, user: str) -> dict:
    try:
        gid = str(json.loads(_body(event)).get("game_id", ""))
    except json.JSONDecodeError:
        gid = ""
    if not GAME_ID.match(gid):
        return reply(400, {"error": "bad game id"})
    refused = _check_game(gid, user)
    if refused:
        return refused
    upload_id = uuid.uuid4().hex
    url = s3.generate_presigned_url("put_object", ExpiresIn=900, Params={
        "Bucket": DATA, "Key": f"incoming/{user}/{gid}/{upload_id}.json", "ContentType": "application/json"})
    return reply(200, {"game_id": gid, "upload_id": upload_id, "put_url": url})


def uploaded(event: dict, user: str, gid: str) -> dict:
    try:
        upload_id = str(json.loads(_body(event)).get("upload_id", ""))
    except json.JSONDecodeError:
        upload_id = ""
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        return reply(400, {"error": "bad upload id"})
    key = f"incoming/{user}/{gid}/{upload_id}.json"
    try:
        raw = s3.get_object(Bucket=DATA, Key=key)["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return reply(400, {"error": "nothing was uploaded with that link"})
    s3.delete_object(Bucket=DATA, Key=key)
    return accept(raw, user, gid, checked=True)


def _count_today(user: str) -> bool:
    """Count this review against the player's daily limit; False once it is reached."""
    key, day = f"usage/{user}.json", time.strftime("%Y-%m-%d", time.gmtime())
    usage = _get_json(DATA, key) or {}
    n = usage.get(day, 0)
    if n >= MAX_PER_DAY:
        return False
    _put_json(DATA, key, {day: n + 1})
    return True


def accept(raw: str, user: str, expected: str | None, checked: bool) -> dict:
    try:
        rec = json.loads(raw)
        if not isinstance(rec, dict) or rec.get("format") != FORMAT:
            raise ValueError(f"not a Thought Coach recording (format {FORMAT})")
        gid = str(rec.get("game_id", ""))
        if not GAME_ID.match(gid):
            raise ValueError("bad game id")
        if not isinstance(rec.get("events"), list):
            raise ValueError("recording has no events")
        if expected and gid != expected:
            raise ValueError("the uploaded recording is for a different game")
    except (ValueError, json.JSONDecodeError) as err:
        return reply(400, {"error": str(err)})
    facts: dict = {}
    refused = _check_game(gid, user, facts) if not checked else None
    if refused:
        return refused
    if checked:
        try:
            game = game_on_lichess(gid)
            facts = game_facts(game.get("tags") or {}, user) if game else {}
        except ValueError:
            facts = {}

    key = f"recordings/{user}/{gid}.json"
    old = _get_json(DATA, key)
    urls = {"status_url": f"/api/u/{user}/games/{gid}", "dashboard_url": f"/u/{user}/games/{gid}",
            "story_url": f"/u/{user}/games/{gid}/story", "home_url": f"/u/{user}/",
            "live_url": f"/live?u={user}&g={gid}"}
    if old is not None and richness(rec) < richness(old):
        return reply(202, {**(status(user, gid) or {"game_id": gid, "state": "done"}), **urls,
                           "kept": "earlier recording"})
    if user != OWNER and not _count_today(user):  # the person running the site has no daily limit
        return reply(429, {"error": f"that's {MAX_PER_DAY} reviews today: the rest wait until tomorrow (UTC)"})
    s3.put_object(Bucket=DATA, Key=key, Body=json.dumps(rec).encode())
    sqs.send_message(QueueUrl=QUEUE, MessageBody=json.dumps({"game_id": gid, "user": user}))
    boot_s = wake_worker()
    events = rec.get("events") or []
    _put_json(SITE, f"status/{user}/{gid}.json", {
        "game_id": gid, "user": user, "state": "queued", "queued_at": time.time(), "cold_start": boot_s > 0, "boot_s": boot_s,
        "facts": facts, "found": {"arrows": sum(1 for e in events if e.get("type") == "shape_add"),
                                  "pickups": sum(1 for e in events if e.get("type") == "pick_up"),
                                  "audio_chunks": len(rec.get("audio") or [])}}, CacheControl="no-store")
    return reply(202, {"game_id": gid, "state": "queued", **urls})


# ---------------------------------------------------------------- routes


def handler(event: dict, context) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    if method == "OPTIONS":
        return reply(204, {})
    if path == "/health":
        return reply(200, {"ok": True, "app": "thoughtcoach"})
    if method == "GET":
        if path == "/api/login":
            return login(event)
        if path == "/api/callback":
            return callback(event)
        if path == "/api/me":
            return me(event)
        if path == "/api/logout":
            return logout(event)
        m = re.fullmatch(r"/api/u/([a-z0-9_-]{2,30})/active", path)
        if m:
            return reply(200, {"active": active(m.group(1))})
        m = re.fullmatch(r"/api/u/([a-z0-9_-]{2,30})/games/([A-Za-z0-9]{8})", path)
        if m:
            s = status(m.group(1), m.group(2))
            return reply(200, s) if s else reply(404, {"error": "unknown game"})
        m = re.fullmatch(r"/api/games/([A-Za-z0-9]{8})", path)  # extensions before 0.8 (the owner's games)
        if m and OWNER:
            s = status(OWNER, m.group(1))
            return reply(200, s) if s else reply(404, {"error": "unknown game"})
        return reply(404, {"error": "not found"})
    if method != "POST":
        return reply(405, {"error": "method not allowed"})
    if path == "/api/account/delete":
        return delete_account(event)
    user = uploader(event)
    if not user:
        return reply(401, {"error": "not connected: sign in at thoughtcoach.org/start to connect the extension"})
    if path == "/api/uploads":
        return signed_upload(event, user)
    m = re.fullmatch(r"/api/recordings/([A-Za-z0-9]{8})/uploaded", path)
    if m:
        return uploaded(event, user, m.group(1))
    if path == "/api/recordings":
        return accept(_body(event), user, None, checked=False)
    return reply(404, {"error": "not found"})
