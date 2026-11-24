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
BACKFILL = os.environ.get("TC_BACKFILL_URL", "")  # low priority: games that ended a while ago
BURST = os.environ.get("TC_BURST_GROUP", "")  # spot servers for a backlog
FRESH_HOURS = 6  # a game that ended this recently goes to the front
PER_SERVER, MAX_BURST = 4, 3
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


FEEDBACK_REASONS = {"wrong", "confusing", "other"}
PLAYER = re.compile(r"^[a-z0-9_-]{2,30}$")


def feedback(event: dict) -> dict:
    """'This doesn't make sense' from a story card: kept privately (data bucket, feedback/<day>/) so
    we can fix what confuses people. Anyone who can open a story can send one; signed-in players
    are named, others are 'anonymous'. Only for story pages that exist."""
    raw = _body(event)
    if len(raw) > 8000:
        return reply(413, {"error": "too long"})
    try:
        f = json.loads(raw)
        owner, gid, reason = str(f.get("owner", "")).lower(), str(f.get("game_id", "")), f.get("reason")
        if not PLAYER.match(owner) or not GAME_ID.match(gid) or reason not in FEEDBACK_REASONS:
            raise ValueError
    except (ValueError, json.JSONDecodeError, AttributeError):
        return reply(400, {"error": "bad report"})
    try:
        s3.head_object(Bucket=SITE, Key=f"u/{owner}/games/{gid}/story/index.html")
    except Exception:  # noqa: BLE001
        return reply(404, {"error": "no such story"})
    card = f.get("card") if isinstance(f.get("card"), dict) else {}
    record = {
        "at": time.time(), "owner": owner, "game_id": gid, "reason": reason,
        "comment": str(f.get("comment") or "")[:1000], "by": signed_in(event) or "anonymous",
        "card": {"index": card.get("index") if isinstance(card.get("index"), int) else None,
                 "kind": str(card.get("kind") or "")[:40], "ply": card.get("ply") if isinstance(card.get("ply"), int) else None,
                 "title": str(card.get("title") or "")[:200], "text": str(card.get("text") or "")[:1500]},
    }
    _put_json(DATA, f"feedback/{time.strftime('%Y-%m-%d', time.gmtime())}/{owner}-{gid}-{uuid.uuid4().hex[:8]}.json", record)
    return reply(201, {"ok": True})


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


def fresh(rec: dict) -> bool:
    """Did the game end in the last FRESH_HOURS (the extension's end time)? Bulk uploads of old games
    and re-reviews don't: they go to the backfill queue, behind every game just played."""
    when = rec.get("ended_at") or rec.get("started_at")
    if not when:
        return True
    try:
        if isinstance(when, (int, float)):
            t = when / 1000 if when > 1e11 else when
        else:
            t = time.mktime(time.strptime(str(when)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, OverflowError):
        return True
    return time.time() - t < FRESH_HOURS * 3600


def grow_burst() -> None:
    """With a backlog, start spot servers as well: one more per PER_SERVER games waiting."""
    if not BURST:
        return
    depth = 0
    for q in (QUEUE, BACKFILL):
        if q:
            a = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
            depth += int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])
    want = max(0, min(MAX_BURST, -(-depth // PER_SERVER) - 1))
    now = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[BURST])["AutoScalingGroups"][0]["DesiredCapacity"]
    if want > now:
        asg.set_desired_capacity(AutoScalingGroupName=BURST, DesiredCapacity=want, HonorCooldown=False)


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
    queue = QUEUE if fresh(rec) or not BACKFILL else BACKFILL
    sqs.send_message(QueueUrl=queue, MessageBody=json.dumps({"game_id": gid, "user": user}))
    boot_s = wake_worker()
    try:
        grow_burst()
    except Exception:  # noqa: BLE001 - extra servers are a nicety; the warm one is on its way
        pass
    events = rec.get("events") or []
    _put_json(SITE, f"status/{user}/{gid}.json", {
        "game_id": gid, "user": user, "state": "queued", "queued_at": time.time(), "cold_start": boot_s > 0, "boot_s": boot_s,
        "facts": facts, "found": {"arrows": sum(1 for e in events if e.get("type") == "shape_add"),
                                  "pickups": sum(1 for e in events if e.get("type") == "pick_up"),
                                  "audio_chunks": len(rec.get("audio") or [])}}, CacheControl="no-store")
    return reply(202, {"game_id": gid, "state": "queued", **urls})


# ---------------------------------------------------------------- routes


def _list(bucket: str, prefix: str, limit: int = 2000) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out += page.get("Contents", [])
        if len(out) >= limit:
            break
    return out


EVENTS = {"home", "sample_open", "sample_story", "sample_review", "sample_end", "get_started", "start_page",
          "store_click", "profile_view"}


def count_event(event: dict) -> dict:
    """An anonymous page event ('sample_open'): one daily counter per name, kept in the data bucket.
    No cookie, no user id, no IP, no page content: just how many times each step happened that day,
    to see where people stop between the home page and their first review."""
    name = _body(event)[:40].strip()
    if name not in EVENTS:
        return reply(400, {"error": "unknown event"})
    key = f"events/{time.strftime('%Y-%m-%d', time.gmtime())}.json"
    counts = _get_json(DATA, key) or {}
    counts[name] = counts.get(name, 0) + 1  # (a rare lost count under load is fine for this)
    _put_json(DATA, key, counts)
    return reply(204, {})


def funnel(days: int = 14) -> list[tuple[str, dict]]:
    """[(day, counts)] for the last `days` days, newest first: the page events plus, from what's
    stored, sign-ups and finished reviews that day."""
    out = []
    for i in range(days):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * i))
        out.append((day, _get_json(DATA, f"events/{day}.json") or {}))
    return out


def admin(event: dict) -> dict:
    """Everything at a glance, for the owner only (anyone else gets 'not found'): players, every
    game submitted and its review, the 'doesn't make sense' reports, and the queues and servers."""
    import html as H

    if not OWNER or signed_in(event) != OWNER:
        return reply(404, {"error": "not found"})
    esc = lambda x: H.escape(str(x if x is not None else ""))  # noqa: E731
    when = lambda t: time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) if t else ""  # noqa: E731
    day = time.strftime("%Y-%m-%d", time.gmtime())

    users = []
    for o in _list(DATA, "users/"):
        u = _get_json(DATA, o["Key"]) or {}
        users.append(u)
    games = []
    for o in _list(SITE, "status/"):
        parts = o["Key"].split("/")
        if len(parts) != 3 or not parts[2].endswith(".json"):
            continue
        st = _get_json(SITE, o["Key"]) or {}
        st.setdefault("user", parts[1])
        st.setdefault("game_id", parts[2][:-5])
        st["_t"] = st.get("updated") or st.get("finished_at") or st.get("queued_at") or o["LastModified"].timestamp()
        games.append(st)
    games.sort(key=lambda g: -g["_t"])
    reports = []
    for o in sorted(_list(DATA, "feedback/"), key=lambda o: o["LastModified"], reverse=True)[:40]:
        reports.append(_get_json(DATA, o["Key"]) or {})

    per = {}
    for g in games:
        p = per.setdefault(g["user"], {"games": 0, "done": 0, "failed": 0, "last": 0})
        p["games"] += 1
        p["done"] += g.get("state") == "done"
        p["failed"] += g.get("state") == "error"
        p["last"] = max(p["last"], g["_t"])
    rows_u = "".join(
        f"<tr><td><a href='/u/{esc(u.get('id'))}/'>{esc(u.get('name') or u.get('id'))}</a></td>"
        f"<td>{when(u.get('created'))}</td><td>{per.get(u.get('id'), {}).get('games', 0)}</td>"
        f"<td>{per.get(u.get('id'), {}).get('done', 0)}</td><td class='bad'>{per.get(u.get('id'), {}).get('failed', 0) or ''}</td>"
        f"<td>{(_get_json(DATA, f'usage/{u.get(chr(105) + chr(100))}.json') or {}).get(day, 0)}</td>"
        f"<td>{when(per.get(u.get('id'), {}).get('last'))}</td></tr>"
        for u in sorted(users, key=lambda u: -per.get(u.get("id"), {}).get("last", 0)))
    rows_g = "".join(
        f"<tr class='{esc(g.get('state'))}'><td>{when(g['_t'])}</td><td>{esc(g['user'])}</td>"
        f"<td><a href='https://lichess.org/{esc(g['game_id'])}' target='_blank' rel='noopener'>{esc(g['game_id'])}</a></td>"
        f"<td>{esc((g.get('facts') or {}).get('opp'))}</td><td>{esc((g.get('facts') or {}).get('result'))}</td>"
        f"<td>{esc(g.get('state'))}{(' · ' + esc(g.get('stage'))) if g.get('state') == 'running' and g.get('stage') else ''}</td>"
        f"<td>" + (f"<a href='/u/{esc(g['user'])}/games/{esc(g['game_id'])}/story'>story</a> · "
                   f"<a href='/u/{esc(g['user'])}/games/{esc(g['game_id'])}/'>review</a>" if g.get('state') == 'done' else "") +
        f"</td><td class='err'>{esc((g.get('error') or '')[:140]) if g.get('state') == 'error' else ''}</td></tr>"
        for g in games[:500])
    rows_f = "".join(
        f"<tr><td>{when(r.get('at'))}</td><td>{esc(r.get('by'))}</td>"
        f"<td><a href='/u/{esc(r.get('owner'))}/games/{esc(r.get('game_id'))}/story'>{esc(r.get('owner'))}/{esc(r.get('game_id'))}</a></td>"
        f"<td>{esc((r.get('card') or {}).get('title'))}</td><td>{esc(r.get('reason'))}</td><td>{esc(r.get('comment'))}</td></tr>"
        for r in reports) or "<tr><td colspan='6'>No reports yet.</td></tr>"

    def depth(q: str) -> str:
        if not q:
            return "-"
        a = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return f"{a['ApproximateNumberOfMessages']} waiting, {a['ApproximateNumberOfMessagesNotVisible']} being reviewed"

    def servers(group: str) -> str:
        if not group:
            return "-"
        g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[group])["AutoScalingGroups"][0]
        return f"{len(g['Instances'])} running (wanted {g['DesiredCapacity']}, at most {g['MaxSize']})"

    done = sum(g.get("state") == "done" for g in games)
    failed = sum(g.get("state") == "error" for g in games)
    # The way in, day by day: anonymous page events, then sign-ups and first finished reviews (the
    # number that matters: a player who got their own review back).
    first_done = {}
    for g in games:
        if g.get("state") == "done":
            first_done[g["user"]] = min(first_done.get(g["user"], g["_t"]), g["_t"])
    ymd = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))  # noqa: E731
    cols = ["home", "sample_open", "sample_story", "sample_review", "sample_end", "get_started", "start_page", "store_click"]
    rows_e = "".join(
        f"<tr><td>{d}</td>" + "".join(f"<td>{c.get(k, '') or ''}</td>" for k in cols)
        + f"<td>{sum(1 for u in users if u.get('created') and ymd(u['created']) == d) or ''}</td>"
        + f"<td><b>{sum(1 for t in first_done.values() if ymd(t) == d) or ''}</b></td><td>{c.get('profile_view', '') or ''}</td></tr>"
        for d, c in funnel())
    page = f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Admin · Thought Coach</title>
<link rel="icon" href="/favicon.png"><link rel="stylesheet" href="/site.css"><style>
main {{ max-width:1500px; }} h2 {{ margin:28px 0 8px; }} .k {{ display:flex; gap:12px; flex-wrap:wrap; }}
.k div {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 14px; }}
.k b {{ display:block; font-size:22px; }} .k span {{ color:var(--fg2); font-size:13px; }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px; }} th, td {{ text-align:left; padding:6px 8px;
  border-bottom:1px solid var(--line); vertical-align:top; }} th {{ color:var(--muted); font-weight:600; }}
.scroll {{ overflow-x:auto; }} tr.error td {{ background:#e0313114; }} tr.running td, tr.queued td {{ background:#3987e514; }}
.bad, .err {{ color:#ff8787; }} .err {{ font-family:ui-monospace, Consolas, monospace; font-size:12px; }}
</style></head><body><main>
<nav class="top"><p class="brand"><a href="/"><span class="mark"></span>Thought Coach</a></p><span class="sp"></span>
<a class="link" href="/u/{esc(OWNER)}/">Your games</a></nav>
<h1>Admin</h1><p class="muted">Only you can see this page. Times are UTC.</p>
<div class="k"><div><b>{len(users)}</b><span>players</span></div><div><b>{len(games)}</b><span>games submitted</span></div>
<div><b>{done}</b><span>reviewed</span></div><div><b class="bad">{failed}</b><span>failed</span></div>
<div><b>{len(reports)}</b><span>reports</span></div></div>
<h2>Right now</h2><p>Fresh games: {depth(QUEUE)} · Backlog: {depth(BACKFILL)}<br>
Warm server: {servers(GROUP)} · Spot servers: {servers(BURST)}</p>
<h2>The way in, last 14 days</h2><p class="muted">Anonymous page counts (no cookies or ids), then sign-ups and
first finished reviews from the stored accounts. A first finished review is the number that matters.</p>
<div class="scroll"><table><tr><th>Day</th><th>Home</th><th>Sample opened</th><th>Sample story</th><th>Sample review</th>
<th>Sample to the end</th><th>Get started</th><th>Setup page</th><th>Store click</th><th>Sign-ups</th><th>First review</th>
<th>Own home page</th></tr>{rows_e}</table></div>
<h2>Players</h2><div class="scroll"><table><tr><th>Player</th><th>Joined</th><th>Games</th><th>Reviewed</th><th>Failed</th>
<th>Reviews today</th><th>Last activity</th></tr>{rows_u}</table></div>
<h2>"Doesn't make sense?" reports</h2><div class="scroll"><table><tr><th>When</th><th>From</th><th>Game</th><th>Card</th>
<th>Reason</th><th>Comment</th></tr>{rows_f}</table></div>
<h2>Games, newest first</h2><div class="scroll"><table><tr><th>Updated</th><th>Player</th><th>Game</th><th>Opponent</th>
<th>Result</th><th>State</th><th>Open</th><th>Error</th></tr>{rows_g}</table></div>
</main></body></html>"""
    return {"statusCode": 200, "headers": {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                                           "X-Robots-Tag": "noindex"}, "body": page}


def watchdog() -> dict:
    """Every few minutes (a schedule): reviews waiting with no worker running get one. A worker that
    stops mid-review (a spot server taken back, a crash) leaves its game to come back to the queue
    later, when nothing else would wake a server for it."""
    waiting = 0
    for q in (QUEUE, BACKFILL):
        if q:
            a = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]
            waiting += int(a["ApproximateNumberOfMessages"])
    if not waiting:
        return {"waiting": 0}
    boot = wake_worker()
    try:
        grow_burst()
    except Exception:  # noqa: BLE001
        pass
    return {"waiting": waiting, "woke": boot > 0}


def handler(event: dict, context) -> dict:
    if event.get("tc") == "watchdog":  # the schedule, not a web request
        return watchdog()
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
        if path == "/api/admin":
            return admin(event)
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
    if path == "/api/feedback":
        return feedback(event)
    if path == "/api/event":
        return count_event(event)
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
