"""The hosted review worker: takes review jobs from a queue, runs the normal pipeline, publishes the
pages, and switches its own server off when there is nothing left to do (scale to zero).

    python -m thoughtcoach worker            (on AWS: settings from the environment, below)

Environment:
    TC_QUEUE_URL      SQS queue of review jobs ({"game_id": ..., "user": <lichess id>})
    TC_DATA_BUCKET    private bucket: recordings/<user>/<id>.json in, data/<user>/<id>.* (saved reviews) out
    TC_SITE_BUCKET    bucket served at the domain, each player under u/<user>/: index.html (their games),
                      games/<id>/index.html, games/<id>/story/index.html; status/<user>/<id>.json
    TC_IDLE_MINUTES   switch off after this long with no jobs (default 10)
    TC_BACKFILL_URL   low-priority queue (re-reviews, old games uploaded in bulk): only taken from
                      when the main queue is empty, so a game you just played never waits behind them
    TC_ROLE           "warm" (the on-demand server that waits parked in the warm pool) or "burst" (a spot
                      server started for a backlog: it terminates itself when the queues are empty)
    TC_BURST_GROUP    the burst servers' group: the warm worker grows it while there is a backlog

Privacy: once a game is reviewed, its audio is removed from the stored recording; only the
transcript (text) is kept. Fair play: the upload API only queues games lichess says are over.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path

WORK = Path(os.environ.get("TC_WORK", "/data"))
SAVED = (".gaps.json", ".json", ".thinking.json", ".transcript.json", ".tags.json", ".story.json")  # kept between games


def _aws():
    import boto3

    return boto3.client("s3"), boto3.client("sqs")


def _read_status(s3, site: str, user: str, gid: str) -> dict:
    try:
        return json.loads(s3.get_object(Bucket=site, Key=f"status/{user}/{gid}.json")["Body"].read())
    except Exception:  # noqa: BLE001 - none yet
        return {}


def _status(s3, site: str, user: str, gid: str, state: str, **extra) -> None:
    """The review's status, keeping what is already there (the game facts the upload API wrote,
    what the live page has been shown)."""
    body = {**_read_status(s3, site, user, gid), "game_id": gid, "user": user, "state": state,
            "updated": time.time(), **extra}
    if state != "error":
        body.pop("error", None)  # a review that runs (again) is no longer the earlier failure
    if state == "done":
        body.update(dashboard=f"/u/{user}/games/{gid}", story=f"/u/{user}/games/{gid}/story")
    s3.put_object(Bucket=site, Key=f"status/{user}/{gid}.json", Body=json.dumps(body).encode(),
                  ContentType="application/json", CacheControl="no-store")


def _sync_saved(s3, data: str, user: str, out: Path) -> None:
    """This player's earlier saved reviews, so the story's focus can weigh what keeps coming back."""
    out.mkdir(parents=True, exist_ok=True)
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=data, Prefix=f"data/{user}/")
    for page in pages:
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[1]
            target = out / name
            if name.endswith(SAVED) and not target.exists():
                s3.download_file(data, obj["Key"], str(target))
                stamp = obj["LastModified"].timestamp()  # keep when it was reviewed: the home page sorts by it
                os.utime(target, (stamp, stamp))


def _publish(s3, data: str, site: str, user: str, gid: str, out: Path, games: Path) -> None:
    html = {"html": "text/html; charset=utf-8"}
    # no-cache: the CDN checks for a newer copy each time, so a game reviewed again shows at once.
    page = {"ContentType": html["html"], "CacheControl": "no-cache"}
    s3.upload_file(str(out / f"{gid}.html"), site, f"u/{user}/games/{gid}/index.html", ExtraArgs=page)
    s3.upload_file(str(out / f"{gid}.story.html"), site, f"u/{user}/games/{gid}/story/index.html", ExtraArgs=page)
    for suffix in SAVED:
        path = out / f"{gid}{suffix}"
        if path.exists():
            s3.upload_file(str(path), data, f"data/{user}/{gid}{suffix}")
    # The home page: every game reviewed so far, with the patterns across them.
    from .server import Reviews, list_page

    reviews = Reviews(out.parent / "recordings", out, games)
    # This server only holds the recording it just reviewed; every game with a saved review is done.
    reviews.status = {p.name.split(".")[0]: {"game_id": p.name.split(".")[0], "state": "done"}
                      for p in out.glob("*.gaps.json")}
    page = list_page(reviews, owner=user)
    s3.put_object(Bucket=site, Key=f"u/{user}/index.html", Body=page.encode("utf-8"), ContentType=html["html"],
                  CacheControl="no-cache")


def _strip_audio(s3, data: str, user: str, gid: str, path: Path) -> None:
    recording = json.loads(path.read_text(encoding="utf-8"))
    if recording.get("audio"):
        recording["audio"] = []
        recording["audio_removed_after_review"] = True
        s3.put_object(Bucket=data, Key=f"recordings/{user}/{gid}.json",
                      Body=json.dumps(recording).encode())


class Live:
    """Publishes a running review's progress for the live page: the stage, and what each stage has
    found so far (the win-chance curve move by move, the arrows, what was said)."""

    EVERY = 2.5  # seconds between writes (a stage change writes at once)

    def __init__(self, s3, site: str, user: str, gid: str):
        self.s3, self.site, self.user, self.gid = s3, site, user, gid
        self.status = {**_read_status(s3, site, user, gid), "game_id": gid, "user": user, "state": "running",
                       "started_at": time.time(), "stage": "game", "found": {}, "moves": [], "engine": None}
        self.last = 0.0
        self._write()

    def update(self, stage: str, data: dict) -> None:
        changed = stage != self.status["stage"]
        self.status["stage"] = stage
        if "move" in data:
            self.status["moves"].append(data.pop("move"))
        if "moves" in data:
            self.status["moves"] = data.pop("moves")
        if "done" in data:
            self.status["engine"] = [data.pop("done"), data.pop("total", None)]
        self.status["found"].update(data)
        if changed or time.time() - self.last >= self.EVERY:
            self._write()

    def _write(self) -> None:
        self.last = time.time()
        self.status["updated"] = self.last
        self.s3.put_object(Bucket=self.site, Key=f"status/{self.user}/{self.gid}.json",
                           Body=json.dumps(self.status).encode(), ContentType="application/json",
                           CacheControl="no-store")


USER = re.compile(r"^[a-z0-9_-]{2,30}$")  # a lichess id (lowercase username)


def review_one(s3, data: str, site: str, user: str, gid: str) -> None:
    from . import fmt, progress
    from .__main__ import build_parser, cmd_review

    if not USER.match(user):
        raise ValueError(f"bad user id {user!r}")
    home = WORK / user  # each player's reviews apart: their patterns are theirs alone
    rec_dir, out, games = home / "recordings", home / "out", WORK / "games"
    for d in (rec_dir, out, games):
        d.mkdir(parents=True, exist_ok=True)
    live = Live(s3, site, user, gid)
    path = rec_dir / f"{gid}.json"
    s3.download_file(data, f"recordings/{user}/{gid}.json", str(path))
    _sync_saved(s3, data, user, out)
    if json.loads(path.read_text(encoding="utf-8")).get("audio"):
        (out / f"{gid}.transcript.json").unlink(missing_ok=True)  # new audio: transcribe it again
    fmt.SITE["base"] = f"/u/{user}"
    args = build_parser().parse_args(["review", str(path), "--out", str(out), "--games", str(games)])
    progress.HOOK = live.update
    try:
        cmd_review(args)
    finally:
        progress.HOOK = None
    _publish(s3, data, site, user, gid, out, games)
    _strip_audio(s3, data, user, gid, path)
    _status(s3, site, user, gid, "done", finished_at=time.time())


PUZZLE_JOB = re.compile(r"^[A-Za-z0-9-]{8,40}$")


def puzzle_voice_one(s3, data: str, user: str, jid: str) -> None:
    """A recorded puzzle solve: what was said, as moves on the puzzle's board. The upload (with the audio) is
    deleted whatever happens; the result (text and moves) is kept for the player's page to fetch."""
    from .puzzle_voice import process

    if not USER.match(user) or not PUZZLE_JOB.match(jid):
        raise ValueError(f"bad puzzle job {user!r} {jid!r}")
    key = f"puzzles/voice/{user}/{jid}"
    try:
        job = json.loads(s3.get_object(Bucket=data, Key=f"{key}.json")["Body"].read())
        try:
            result = {"state": "done", **process(job)}
        except Exception as err:  # noqa: BLE001 - the page shows the arrows' comparison anyway
            traceback.print_exc()
            result = {"state": "error", "error": f"{type(err).__name__}: {err}"}
        result.update(puzzle=job.get("puzzle"), finished=time.time())
        s3.put_object(Bucket=data, Key=f"{key}.result.json", Body=json.dumps(result).encode(), ContentType="application/json")
    finally:
        s3.delete_object(Bucket=data, Key=f"{key}.json")  # the audio: never kept


PER_SERVER = 4  # queued games per server before another one is worth starting
MAX_BURST = 3


def _depth(sqs, *queues) -> int:
    """Games waiting or being reviewed, across the queues."""
    total = 0
    for q in queues:
        if q:
            a = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
            total += int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])
    return total


def burst_wanted(depth: int) -> int:
    """Spot servers worth adding for this many games: one server per PER_SERVER, the warm one first."""
    return max(0, min(MAX_BURST, -(-depth // PER_SERVER) - 1))


def _grow_burst(sqs, queues) -> None:
    """While there is a backlog, make sure enough burst servers are on their way (never shrinks:
    burst servers switch themselves off when the queues are empty)."""
    group = os.environ.get("TC_BURST_GROUP")
    if not group:
        return
    import boto3

    try:
        want = burst_wanted(_depth(sqs, *queues))
        asg = boto3.client("autoscaling")
        now = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[group])["AutoScalingGroups"][0]["DesiredCapacity"]
        if want > now:
            asg.set_desired_capacity(AutoScalingGroupName=group, DesiredCapacity=want, HonorCooldown=False)
            print(f"backlog: {want} burst server(s)", file=sys.stderr)
    except Exception as err:  # noqa: BLE001 - scaling is a nicety; reviewing goes on
        print(f"burst scaling skipped: {err}", file=sys.stderr)


def _instance_id() -> str:
    token = urllib.request.urlopen(urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=2).read().decode()
    return urllib.request.urlopen(urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token}), timeout=2).read().decode()


def _switch_off() -> None:
    """Scale the worker group to 0. With the warm pool this server goes back to it, stopped, with the
    image and models still on its disk (the next review starts it in under a minute). Without a group
    name (an older launch setup), leave the group the old way: terminate this server."""
    import boto3

    group = os.environ.get("TC_WORKER_GROUP")
    asg = boto3.client("autoscaling")
    if os.environ.get("TC_ROLE") == "burst":  # a spot server: gone for good, and one fewer wanted
        asg.terminate_instance_in_auto_scaling_group(InstanceId=_instance_id(), ShouldDecrementDesiredCapacity=True)
        return
    if group:
        asg.set_desired_capacity(AutoScalingGroupName=group, DesiredCapacity=0, HonorCooldown=False)
        return
    asg.terminate_instance_in_auto_scaling_group(InstanceId=_instance_id(), ShouldDecrementDesiredCapacity=True)


def run() -> int:
    queue, data, site = os.environ["TC_QUEUE_URL"], os.environ["TC_DATA_BUCKET"], os.environ["TC_SITE_BUCKET"]
    idle_limit = float(os.environ.get("TC_IDLE_MINUTES", "10")) * 60
    s3, sqs = _aws()
    last_job = time.time()
    print("Thought Coach worker: waiting for games", file=sys.stderr)
    backfill = os.environ.get("TC_BACKFILL_URL")
    warm = os.environ.get("TC_ROLE", "warm") == "warm"
    while True:
        # Fresh games first: the backfill queue only when the main one has nothing.
        source = queue
        got = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=1, WaitTimeSeconds=5 if backfill else 20,
                                  VisibilityTimeout=1800).get("Messages", [])
        if not got and backfill:
            source = backfill
            got = sqs.receive_message(QueueUrl=backfill, MaxNumberOfMessages=1, WaitTimeSeconds=15,
                                      VisibilityTimeout=1800).get("Messages", [])
        if got and warm:
            _grow_burst(sqs, (queue, backfill))
        if not got:
            if time.time() - last_job > idle_limit:
                print("idle: switching off", file=sys.stderr)
                _switch_off()
                return 0
            continue
        msg = got[0]
        job = json.loads(msg["Body"])
        if job.get("type") == "puzzle_voice":  # a puzzle solved out loud: seconds, not a review
            print(f"puzzle voice {job.get('job')} for {job.get('user')}", file=sys.stderr)
            try:
                puzzle_voice_one(s3, data, job.get("user", ""), job.get("job", ""))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            sqs.delete_message(QueueUrl=source, ReceiptHandle=msg["ReceiptHandle"])
            last_job = time.time()
            continue
        gid, user = job["game_id"], job.get("user", "")
        print(f"reviewing {gid} for {user}", file=sys.stderr)
        try:
            review_one(s3, data, site, user, gid)
        except Exception as err:  # report on the game; the job is not retried forever
            traceback.print_exc()
            if USER.match(user):
                _status(s3, site, user, gid, "error", error=f"{type(err).__name__}: {err}")
        sqs.delete_message(QueueUrl=source, ReceiptHandle=msg["ReceiptHandle"])
        last_job = time.time()
