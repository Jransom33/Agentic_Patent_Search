#!/usr/bin/env bash
# One-time, idempotent setup for one component VM. update.sh --setup uploads
# and runs this on the VM as root:
#   sudo bash /tmp/setup_vm.sh <intake|search|report>
set -euo pipefail
COMPONENT="${1:?usage: setup_vm.sh <intake|search|report>}"

REMOTE_DIR="/opt/agentic-patents"
SERVICE_USER="patents"
ENV_DIR="/etc/agentic-patents"
ENV_FILE="$ENV_DIR/$COMPONENT.env"
UNIT="patents-$COMPONENT"

echo "--- python (Ubuntu 24.04 ships 3.12; venv module comes from apt) ---"
apt-get update -q
apt-get install -yq python3.12-venv

echo "--- dedicated non-root service user (spec §15) ---"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home "$SERVICE_USER"

echo "--- code directory and virtualenv ---"
mkdir -p "$REMOTE_DIR"
chown "$SERVICE_USER:" "$REMOTE_DIR"
[ -d "$REMOTE_DIR/.venv" ] \
  || sudo -u "$SERVICE_USER" python3.12 -m venv "$REMOTE_DIR/.venv"

echo "--- env file template (never overwritten once created) ---"
# Secrets are hand-filled here on the VM and stay out of Git. Root-owned and
# mode 600; systemd reads it as root before dropping to the service user.
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  {
    echo "APP_ENV=gcp"
    echo "GCP_PROJECT=FILL_ME"
    echo "PUBSUB_SEARCH_PLANS_TOPIC=search-plans"
    echo "PUBSUB_CANDIDATES_TOPIC=candidates"
    if [ "$COMPONENT" = "search" ]; then
      # Component B: plans subscription, no Cloud SQL DSN (spec §8).
      echo "PUBSUB_SEARCH_PLANS_SUBSCRIPTION=search-plans-sub"
    else
      echo "PUBSUB_CANDIDATES_SUBSCRIPTION=candidates-sub"
      echo "CLOUD_SQL_DSN=postgresql://postgres:FILL_ME@FILL_ME:5432/patents"
    fi
    echo "REDIS_HOST=FILL_ME"
    echo "ANTHROPIC_API_KEY=FILL_ME"
    echo "EXA_API_KEY=FILL_ME"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo ">>> fill in $ENV_FILE before starting $UNIT"
fi

echo "--- systemd unit ---"
cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=Agentic patents $COMPONENT component
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$REMOTE_DIR/.venv/bin/python -m $COMPONENT.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$UNIT"
echo "--- setup done for $COMPONENT ---"
