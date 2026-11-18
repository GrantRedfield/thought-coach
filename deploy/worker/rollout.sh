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
QUEUE=$(aws cloudformation describe-stack-resources --stack-name ThoughtCoach \
  --query "StackResources[?ResourceType=='AWS::SQS::Queue' && starts_with(LogicalResourceId, 'Reviews')].PhysicalResourceId" --output text)

BUILD=$(aws codebuild start-build --project-name "$PROJECT" --query build.id --output text)
echo "building $BUILD"
while :; do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD" --query 'builds[0].buildStatus' --output text)
  [ "$STATUS" != IN_PROGRESS ] && break
  sleep 20
done
echo "build: $STATUS"
[ "$STATUS" = SUCCEEDED ] || exit 1

# Park the worker: scale to 0 and wait until no server is in service (a review in progress goes
# back on the queue and is picked up again by the next start).
aws autoscaling set-desired-capacity --auto-scaling-group-name "$GROUP" --desired-capacity 0
for _ in $(seq 60); do
  N=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$GROUP" \
    --query 'length(AutoScalingGroups[0].Instances)' --output text)
  [ "$N" = 0 ] && break
  sleep 10
done
echo "worker parked"

if [ $# -ge 2 ]; then
  USER_ID=$1; shift
  for GID in "$@"; do
    aws sqs send-message --queue-url "$QUEUE" --message-body "{\"game_id\": \"$GID\", \"user\": \"$USER_ID\"}" \
      --query MessageId --output text >/dev/null
    echo "queued $GID"
  done
  aws autoscaling set-desired-capacity --auto-scaling-group-name "$GROUP" --desired-capacity 1
  echo "worker started"
fi
