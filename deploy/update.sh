#!/usr/bin/env bash
# Deploy the current local git commit to all three VMs and restart services.
#
# Usage:
#   PROJECT_ID=my-project ./update.sh            # routine code update
#   PROJECT_ID=my-project ./update.sh --setup    # first time: also run setup_vm.sh
#
# Ships `git archive HEAD` rather than pulling from GitHub: the VMs need no
# repo credentials, and all three provably run the same commit (mismatched
# shared/ models between VMs could publish messages the others reject).
# Commit your changes first; uncommitted work is intentionally not deployed.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.sh

SETUP=false
[ "${1:-}" = "--setup" ] && SETUP=true

ARCHIVE=/tmp/agentic_patents.tar.gz
git -C .. archive --format=tar.gz -o "$ARCHIVE" HEAD

for comp in "${COMPONENTS[@]}"; do
  vm="$(vm_name "$comp")"
  echo "--- deploying $(git -C .. rev-parse --short HEAD) to $vm ---"
  GC compute scp "$ARCHIVE" "$vm:/tmp/" --zone="$ZONE"

  if $SETUP; then
    GC compute scp setup_vm.sh "$vm:/tmp/" --zone="$ZONE"
    GC compute ssh "$vm" --zone="$ZONE" --command="sudo bash /tmp/setup_vm.sh $comp"
  fi

  # Unpack over the previous checkout, install deps into the persistent venv,
  # then restart. The restart is allowed to fail on first deploy, when the
  # env file still has FILL_ME placeholders.
  GC compute ssh "$vm" --zone="$ZONE" --command="
    sudo tar -xzf /tmp/agentic_patents.tar.gz -C $REMOTE_DIR &&
    sudo chown -R $SERVICE_USER: $REMOTE_DIR &&
    cd $REMOTE_DIR &&
    sudo -u $SERVICE_USER .venv/bin/pip install -q -r requirements.txt &&
    (sudo systemctl restart patents-$comp \
      || echo '>>> restart failed; is /etc/agentic-patents/$comp.env filled in?')"
done
echo "--- done ---"
