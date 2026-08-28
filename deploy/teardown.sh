#!/usr/bin/env bash
# Delete every billed GCP resource created by provision.sh so the class
# project stops costing money. Each delete is best-effort (|| true) so the
# script is safe to re-run or to run after a partial provision.
#
# Usage:
#   PROJECT_ID=my-project ./teardown.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh

echo "--- VMs ---"
for comp in "${COMPONENTS[@]}"; do
  GC compute instances delete "$(vm_name "$comp")" --zone="$ZONE" --quiet || true
done

echo "--- Memorystore and Cloud SQL ---"
GC redis instances delete "$REDIS_INSTANCE" --region="$REGION" --quiet || true
GC sql instances delete "$SQL_INSTANCE" --quiet || true

echo "--- Pub/Sub ---"
GC pubsub subscriptions delete "$PLANS_SUB" --quiet || true
GC pubsub subscriptions delete "$CANDIDATES_SUB" --quiet || true
GC pubsub topics delete "$PLANS_TOPIC" --quiet || true
GC pubsub topics delete "$CANDIDATES_TOPIC" --quiet || true

echo "--- service accounts ---"
for comp in "${COMPONENTS[@]}"; do
  GC iam service-accounts delete "$(sa_email "$comp")" --quiet || true
done

# The VPC peering and its reserved address range are free, and other services
# in the project may share them, so they are intentionally left in place.
echo "--- teardown done ---"
