#!/usr/bin/env bash
# Create every GCP resource the system needs (spec §11). Re-runnable: each
# resource is created only if it does not already exist.
#
# Usage:
#   PROJECT_ID=my-project DB_PASSWORD=... ./provision.sh
#
# Afterwards run ./update.sh --setup, fill in each VM's env file with the
# values this script prints, then start the services.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh
: "${DB_PASSWORD:?set DB_PASSWORD for the Cloud SQL postgres user}"

echo "--- enabling required APIs ---"
GC services enable pubsub.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com compute.googleapis.com servicenetworking.googleapis.com

echo "--- Pub/Sub topics and subscriptions ---"
# 600s is the maximum ack deadline: Component B's search can run for minutes,
# and shared/pubsub.py does not extend leases mid-run.
GC pubsub topics describe "$PLANS_TOPIC" >/dev/null 2>&1 \
  || GC pubsub topics create "$PLANS_TOPIC"
GC pubsub topics describe "$CANDIDATES_TOPIC" >/dev/null 2>&1 \
  || GC pubsub topics create "$CANDIDATES_TOPIC"
GC pubsub subscriptions describe "$PLANS_SUB" >/dev/null 2>&1 \
  || GC pubsub subscriptions create "$PLANS_SUB" --topic="$PLANS_TOPIC" --ack-deadline=600
GC pubsub subscriptions describe "$CANDIDATES_SUB" >/dev/null 2>&1 \
  || GC pubsub subscriptions create "$CANDIDATES_SUB" --topic="$CANDIDATES_TOPIC" --ack-deadline=600

echo "--- service accounts (one per component, least privilege) ---"
for comp in "${COMPONENTS[@]}"; do
  GC iam service-accounts describe "$(sa_email "$comp")" >/dev/null 2>&1 \
    || GC iam service-accounts create "patents-$comp" --display-name="Component $comp"
done
# Bindings are per-topic / per-subscription, not project-wide, and re-running
# add-iam-policy-binding with the same member+role is a harmless no-op.
# A publishes plans; B consumes plans and publishes candidates; C consumes
# candidates. B gets no Cloud SQL-related access at all (spec §8).
GC pubsub topics add-iam-policy-binding "$PLANS_TOPIC" \
  --member="serviceAccount:$(sa_email intake)" --role=roles/pubsub.publisher
GC pubsub subscriptions add-iam-policy-binding "$PLANS_SUB" \
  --member="serviceAccount:$(sa_email search)" --role=roles/pubsub.subscriber
GC pubsub topics add-iam-policy-binding "$CANDIDATES_TOPIC" \
  --member="serviceAccount:$(sa_email search)" --role=roles/pubsub.publisher
GC pubsub subscriptions add-iam-policy-binding "$CANDIDATES_SUB" \
  --member="serviceAccount:$(sa_email report)" --role=roles/pubsub.subscriber

echo "--- private services access (private IPs for SQL and Redis, spec §15) ---"
GC compute addresses describe google-managed-services-default --global >/dev/null 2>&1 \
  || GC compute addresses create google-managed-services-default \
       --global --purpose=VPC_PEERING --prefix-length=16 --network=default
# The peering connect call fails harmlessly if the peering already exists.
GC services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-default --network=default 2>/dev/null \
  || echo "(vpc peering already connected)"

echo "--- Cloud SQL PostgreSQL (private IP only) ---"
# ASSUMPTION: db-g1-small is enough for demo traffic. Takes ~10 minutes.
GC sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1 \
  || GC sql instances create "$SQL_INSTANCE" \
       --database-version=POSTGRES_16 --edition=ENTERPRISE \
       --tier=db-g1-small --region="$REGION" \
       --network=default --no-assign-ip
# FOLLOW-UP: spec §15 wants separate least-privilege DB users; the demo uses
# the built-in postgres user with this password for components A and C.
GC sql users set-password postgres --instance="$SQL_INSTANCE" --password="$DB_PASSWORD"
GC sql databases describe "$SQL_DATABASE" --instance="$SQL_INSTANCE" >/dev/null 2>&1 \
  || GC sql databases create "$SQL_DATABASE" --instance="$SQL_INSTANCE"

echo "--- Memorystore Redis ---"
# Defaults (no AUTH, no in-transit encryption) match RedisSearchCache.
GC redis instances describe "$REDIS_INSTANCE" --region="$REGION" >/dev/null 2>&1 \
  || GC redis instances create "$REDIS_INSTANCE" --size=1 --region="$REGION" --network=default

echo "--- Compute Engine VMs (one per component) ---"
# VMs keep an external IP only for outbound Anthropic/Exa/pip traffic (no
# Cloud NAT in this demo). Nothing listens publicly: the default firewall
# blocks inbound ports and the intake API binds 127.0.0.1 (tunnel access).
# cloud-platform scope defers all authorization to the per-component IAM
# roles granted above.
for comp in "${COMPONENTS[@]}"; do
  GC compute instances describe "$(vm_name "$comp")" --zone="$ZONE" >/dev/null 2>&1 \
    || GC compute instances create "$(vm_name "$comp")" \
         --zone="$ZONE" --machine-type=e2-small \
         --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
         --service-account="$(sa_email "$comp")" --scopes=cloud-platform
done

echo "--- values for the per-VM env files (/etc/agentic-patents/*.env) ---"
SQL_IP="$(GC sql instances describe "$SQL_INSTANCE" --format='value(ipAddresses[0].ipAddress)')"
REDIS_IP="$(GC redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)')"
cat <<SUMMARY
GCP_PROJECT=$PROJECT_ID
PUBSUB_SEARCH_PLANS_TOPIC=$PLANS_TOPIC
PUBSUB_SEARCH_PLANS_SUBSCRIPTION=$PLANS_SUB
PUBSUB_CANDIDATES_TOPIC=$CANDIDATES_TOPIC
PUBSUB_CANDIDATES_SUBSCRIPTION=$CANDIDATES_SUB
REDIS_HOST=$REDIS_IP
CLOUD_SQL_DSN=postgresql://postgres:<DB_PASSWORD>@$SQL_IP:5432/$SQL_DATABASE   (intake/report VMs only)

Next: ./update.sh --setup, fill each VM's env file (plus API keys), then:
  gcloud compute ssh $(vm_name intake) --project $PROJECT_ID --zone $ZONE -- -L 8000:localhost:8000
SUMMARY
