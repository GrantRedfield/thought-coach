"""Read the "doesn't make sense" reports sent from story cards, newest first.

    python deploy/feedback.py            # the last 30 days
    python deploy/feedback.py --days 7

Copies feedback/ from the private data bucket into deploy/.feedback/ (ignored by git: reports can
quote what players wrote) and prints them. Needs the AWS CLI with access to the stack's account.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
OUTPUTS = HERE / "cdk" / "outputs.json"
LOCAL = HERE / ".feedback"
WHY = {"wrong": "wrong / contradicts itself", "confusing": "confusing", "other": "other"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    bucket = json.loads(OUTPUTS.read_text())["ThoughtCoach"]["DataBucket"]
    subprocess.run(["aws", "s3", "sync", f"s3://{bucket}/feedback/", str(LOCAL), "--quiet"], check=True)
    since = time.time() - args.days * 86400
    reports = []
    for p in LOCAL.rglob("*.json"):
        r = json.loads(p.read_text(encoding="utf-8"))
        if r.get("at", 0) >= since:
            reports.append(r)
    reports.sort(key=lambda r: -r["at"])
    print(f"{len(reports)} report(s) in the last {args.days} days\n")
    for r in reports:
        c = r.get("card") or {}
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"]))
        print(f"{when}  {WHY.get(r['reason'], r['reason'])}  ·  {r['owner']}/{r['game_id']}  card {c.get('index')} "
              f"({c.get('kind')}, ply {c.get('ply')})  ·  from {r.get('by')}")
        print(f"  {c.get('title')}")
        if r.get("comment"):
            print(f"  says: {r['comment']}")
        print(f"  https://thoughtcoach.org/u/{r['owner']}/games/{r['game_id']}/story\n")


if __name__ == "__main__":
    main()
