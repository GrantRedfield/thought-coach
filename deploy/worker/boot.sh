#!/bin/bash
# Runs on every start of a worker server (a systemd service), not just the first boot: servers wait
# stopped in the Auto Scaling group's warm pool, with the worker image already on their disk.
#
#   first launch, into the warm pool:  pull the image, report ready, stop (the group stops the server)
#   started for a review (InService):  pull any newer image (only what changed), run the worker;
#                                      when it is idle it scales the group to 0 and the server goes
#                                      back to the warm pool, stopped.
# Settings come from /etc/thoughtcoach.env (written by the launch template's user data).
set -u
. /etc/thoughtcoach.env

imds_token() { curl -sf -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300"; }
md() { curl -sf -H "X-aws-ec2-metadata-token: $(imds_token)" "http://169.254.169.254/latest/meta-data/$1"; }

IID=$(md instance-id)
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
# Old images left behind by earlier pulls fill the disk (each is several GB), and then a pull of new code fails
# and the old code runs: clear them before pulling and after.
docker image prune -f >/dev/null 2>&1 || true
docker pull "$REPO:latest" || echo "pull failed: using the image already here"
docker image prune -f >/dev/null 2>&1 || true

GROUP=$(aws autoscaling describe-auto-scaling-instances --instance-ids "$IID" --region "$REGION" \
  --query 'AutoScalingInstances[0].AutoScalingGroupName' --output text)
STATE=""
for _ in $(seq 60); do
  STATE=$(md autoscaling/target-lifecycle-state || true)
  [ -n "$STATE" ] && break
  sleep 2
done
echo "target lifecycle state: $STATE"
aws autoscaling complete-lifecycle-action --lifecycle-hook-name thoughtcoach-ready --auto-scaling-group-name "$GROUP" \
  --lifecycle-action-result CONTINUE --instance-id "$IID" --region "$REGION" || true

case "$STATE" in
  Warmed:*) exit 0 ;;  # ready and waiting: the group stops this server now
esac

docker run --rm -e TC_QUEUE_URL="$QUEUE" -e TC_DATA_BUCKET="$DATA" -e TC_SITE_BUCKET="$SITE" \
  -e TC_BACKFILL_URL="${BACKFILL:-}" -e TC_ROLE="${ROLE:-warm}" -e TC_BURST_GROUP="${BURST:-}" \
  -e TC_IDLE_MINUTES="${IDLE:-10}" \
  -e TC_WORKER_GROUP="$GROUP" -e AWS_DEFAULT_REGION="$REGION" "$REPO:latest" worker
# The worker scales the group to 0 when idle; if it stopped any other way, do it here so the server
# doesn't keep running.
if [ "${ROLE:-warm}" = burst ]; then  # a spot server: terminate it, one fewer wanted
  aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$IID" --should-decrement-desired-capacity \
    --region "$REGION" || true
else
  aws autoscaling set-desired-capacity --auto-scaling-group-name "$GROUP" --desired-capacity 0 --region "$REGION" || true
fi
