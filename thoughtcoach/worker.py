"""The hosted review worker: takes review jobs from a queue, runs the normal pipeline, publishes the
pages, and switches its own server off when there is nothing left to do (scale to zero).

    python -m thoughtcoach worker            (on AWS: settings from the environment, below)

Environment:
    TC_QUEUE_URL      SQS queue of review jobs ({"game_id": ..., "user": <lichess id>})
    TC_DATA_BUCKET    private bucket: recordings/<user>/<id>.json in, data/<user>/<id>.* (saved reviews) out
    TC_SITE_BUCKET    bucket served at the domain, each player under u/<user>/: index.html (their games),
                      games/<id>/index.html, games/<id>/story/index.html; status/<user>/<id>.json
    TC_IDLE_MINUTES   switch off after this long with no jobs (default 10)

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


def _status(s3, site: str, user: str, gid: str, state: str, **extra) -> None:
    body = {"game_id": gid, "user": user, "state": state, "updated": time.time(), **extra}
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


USER = re.compile(r"^[a-z0-9_-]{2,30}$")  # a lichess id (lowercase username)


def review_one(s3, data: str, site: str, user: str, gid: str) -> None:
    from . import fmt
    from .__main__ import build_parser, cmd_review

    if not USER.match(user):
        raise ValueError(f"bad user id {user!r}")
    home = WORK / user  # each player's reviews apart: their patterns are theirs alone
    rec_dir, out, games = home / "recordings", home / "out", WORK / "games"
    for d in (rec_dir, out, games):
        d.mkdir(parents=True, exist_ok=True)
    _status(s3, site, user, gid, "running", started_at=time.time())
    path = rec_dir / f"{gid}.json"
    s3.download_file(data, f"recordings/{user}/{gid}.json", str(path))
    _sync_saved(s3, data, user, out)
    if json.loads(path.read_text(encoding="utf-8")).get("audio"):
        (out / f"{gid}.transcript.json").unlink(missing_ok=True)  # new audio: transcribe it again
    fmt.SITE["base"] = f"/u/{user}"
    args = build_parser().parse_args(["review", str(path), "--out", str(out), "--games", str(games)])
    cmd_review(args)
    _publish(s3, data, site, user, gid, out, games)
    _strip_audio(s3, data, user, gid, path)
    _status(s3, site, user, gid, "done", finished_at=time.time())


def _switch_off() -> None:
    """Leave the Auto Scaling group (desired capacity down by one), which terminates this server."""
    import boto3

    token = urllib.request.urlopen(urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=2).read().decode()
    iid = urllib.request.urlopen(urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token}), timeout=2).read().decode()
    boto3.client("autoscaling").terminate_instance_in_auto_scaling_group(
        InstanceId=iid, ShouldDecrementDesiredCapacity=True)


def run() -> int:
    queue, data, site = os.environ["TC_QUEUE_URL"], os.environ["TC_DATA_BUCKET"], os.environ["TC_SITE_BUCKET"]
    idle_limit = float(os.environ.get("TC_IDLE_MINUTES", "10")) * 60
    s3, sqs = _aws()
    last_job = time.time()
    print("Thought Coach worker: waiting for games", file=sys.stderr)
    while True:
        got = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                                  VisibilityTimeout=1800).get("Messages", [])
        if not got:
            if time.time() - last_job > idle_limit:
                print("idle: switching off", file=sys.stderr)
                _switch_off()
                return 0
            continue
        msg = got[0]
        job = json.loads(msg["Body"])
        gid, user = job["game_id"], job.get("user", "")
        print(f"reviewing {gid} for {user}", file=sys.stderr)
        try:
            review_one(s3, data, site, user, gid)
        except Exception as err:  # report on the game; the job is not retried forever
            traceback.print_exc()
            if USER.match(user):
                _status(s3, site, user, gid, "error", error=f"{type(err).__name__}: {err}")
        sqs.delete_message(QueueUrl=queue, ReceiptHandle=msg["ReceiptHandle"])
        last_job = time.time()
