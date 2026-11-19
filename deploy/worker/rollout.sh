#!/bin/bash
# Ship new worker code: build the image in AWS, then park any running worker so the next review
# starts on the new image (a running worker keeps the image it started with). Optionally re-review
# games afterwards.
#
#   deploy/worker/rollout.sh                      build + park
#   deploy/worker/rollout.sh lucidchess ID1 ID2   ... and re-review those games of that player
#
# Run `cdk deploy` first: the build uses the source it uploads. Names come from
# deploy/cdk/outputs.json (written by `cdk deploy --outputs-file outputs.json`).
set -euo pipefail
cd "$(dirname "$0")/../cdk"
out() { python -c "import json,sys; print(json.load(open('outputs.json'))['ThoughtCoach'][sys.argv[1]])" "$1"; }
PROJECT=$(out WorkerBuildProject)
GROUP=$(out WorkerGroup)
# Re-reviews go to the backfill queue: games just played always come first.
QUEUE=$(out BackfillQueue)
BURST=$(out BurstGroup)

BUILD=$(aws codebuild start-build --project-name "$PROJECT" --query build.id --output text)
echo "building $BUILD"
while :; do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD" --query 'builds[0].buildStatus' --output text)
  [ "$STATUS" != IN_PROGRESS ] && break
  sleep 20
done
echo "build: $STATUS"
[ "$STATUS" = SUCCEEDED ] || exit 1

# Park every worker, the warm one and any spot servers still about from an earlier backlog: a
# running worker keeps the image it started with. Scale both to 0 and wait until none is in service
# (a review in progress goes back on the queue and is picked up again by the next start).
for G in "$GROUP" "$BURST"; do
  aws autoscaling set-desired-capacity --auto-scaling-group-name "$G" --desired-capacity 0
done
for _ in $(seq 90); do
  N=0
  for G in "$GROUP" "$BURST"; do
    N=$(( N + $(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$G" \
      --query 'length(AutoScalingGroups[0].Instances)' --output text) ))
  done
  [ "$N" = 0 ] && break
  sleep 10
done
echo "workers parked"

if [ $# -ge 2 ]; then
  USER_ID=$1; shift
  for GID in "$@"; do
    aws sqs send-message --queue-url "$QUEUE" --message-body "{\"game_id\": \"$GID\", \"user\": \"$USER_ID\"}" \
      --query MessageId --output text >/dev/null
    echo "queued $GID"
  done
  aws autoscaling set-desired-capacity --auto-scaling-group-name "$GROUP" --desired-capacity 1
  # A backlog also gets spot servers: one per 4 games beyond the warm server's, at most 3.
  WANT=$(( ($# + 3) / 4 - 1 )); [ $WANT -gt 3 ] && WANT=3; [ $WANT -lt 0 ] && WANT=0
  if [ $WANT -gt 0 ]; then aws autoscaling set-desired-capacity --auto-scaling-group-name "$BURST" --desired-capacity $WANT; fi
  echo "worker started (+$WANT spot)"
fi
