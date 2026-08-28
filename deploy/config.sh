# Shared names for every deploy script. Source this; do not run it.
# Set PROJECT_ID (and optionally REGION/ZONE) in the environment first:
#   PROJECT_ID=my-project ./provision.sh

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"

# Pub/Sub names. Topics are published to; subscriptions are pulled from.
PLANS_TOPIC="search-plans"
PLANS_SUB="search-plans-sub"
CANDIDATES_TOPIC="candidates"
CANDIDATES_SUB="candidates-sub"

SQL_INSTANCE="patents-sql"
SQL_DATABASE="patents"
REDIS_INSTANCE="patents-redis"

# One VM + one least-privilege service account per component (spec §15).
COMPONENTS=(intake search report)
vm_name() { echo "patents-$1"; }
sa_email() { echo "patents-$1@${PROJECT_ID}.iam.gserviceaccount.com"; }

# Where the code and service live on each VM (see setup_vm.sh).
REMOTE_DIR="/opt/agentic-patents"
SERVICE_USER="patents"

# Every gcloud call targets the configured project explicitly so these
# scripts never depend on (or change) the operator's gcloud defaults.
GC() { gcloud --project="$PROJECT_ID" "$@"; }
