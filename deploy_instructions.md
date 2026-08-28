# GCP Deployment Instructions

These steps provision and deploy the three processes to separate Compute
Engine VMs. Run all commands from your local machine unless a step says it
runs on a VM.

## 1. Prerequisites

- Install the Google Cloud CLI (`gcloud`).
- Create a GCP project and enable billing.
- Ensure your account can create Compute Engine, Pub/Sub, Cloud SQL,
Memorystore, IAM, and private networking resources.
- Have valid Anthropic and Exa API keys.
- Commit the code you want to deploy. `deploy/update.sh` deliberately deploys
committed `HEAD`; it does not include uncommitted changes.

Authenticate and confirm the project exists:

```bash
gcloud auth login
gcloud projects describe YOUR_PROJECT_ID
```

Run the local test suite before deploying:

```bash
source .venv/bin/activate
pytest -q
```



## 2. Set deployment variables

From the repository root:

```bash
cd deploy
export PROJECT_ID="YOUR_PROJECT_ID"
export REGION="us-central1"
export ZONE="us-central1-a"
```

Create a strong database password and save it in a password manager. A
hexadecimal password avoids characters that would need URL encoding inside
`CLOUD_SQL_DSN`:

```bash
export DB_PASSWORD="$(openssl rand -hex 24)"
```

Keep this shell open until the environment files have been configured.
Re-running `provision.sh` with a different password changes the PostgreSQL
password, so reuse the same value.

## 3. Provision GCP

```bash
./provision.sh
```

The script creates:

- two Pub/Sub topics and two subscriptions;
- one Cloud SQL PostgreSQL instance and database;
- one Memorystore Redis instance;
- three least-privilege service accounts; and
- three Compute Engine VMs (`patents-intake`, `patents-search`, and
`patents-report`).

Cloud SQL and Memorystore can take 10–20 minutes to become ready. Save the
summary printed at the end, especially the Cloud SQL and Redis private IPs.

If the summary is lost, retrieve the addresses with:

```bash
SQL_IP="$(gcloud sql instances describe patents-sql \
  --project="$PROJECT_ID" --format='value(ipAddresses[0].ipAddress)')"
REDIS_IP="$(gcloud redis instances describe patents-redis \
  --project="$PROJECT_ID" --region="$REGION" --format='value(host)')"
```



## 4. Install the application on the VMs

The first deployment also initializes Python, the non-root service account,
the virtual environment, environment-file templates, and systemd:

```bash
./update.sh --setup
```

An initial service restart may fail because the generated environment files
still contain `FILL_ME`. Configure them next.

## 5. Configure Component A (intake)

Connect to the intake VM:

```bash
gcloud compute ssh patents-intake \
  --project="$PROJECT_ID" --zone="$ZONE"
```

On the VM, edit:

```bash
sudoedit /etc/agentic-patents/intake.env
```

Use the values printed by `provision.sh`:

```text
APP_ENV=gcp
GCP_PROJECT=YOUR_PROJECT_ID
PUBSUB_SEARCH_PLANS_TOPIC=search-plans
PUBSUB_CANDIDATES_TOPIC=candidates
PUBSUB_CANDIDATES_SUBSCRIPTION=candidates-sub
CLOUD_SQL_DSN=postgresql://postgres:YOUR_DB_PASSWORD@CLOUD_SQL_PRIVATE_IP:5432/patents
REDIS_HOST=REDIS_PRIVATE_IP
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_KEY
EXA_API_KEY=YOUR_EXA_KEY
```

The shared A/C settings currently require the candidates subscription, Redis
host, and Exa key even though intake does not use all of them directly.

Restrict the file and start the service:

```bash
sudo chmod 600 /etc/agentic-patents/intake.env
sudo systemctl restart patents-intake
sudo systemctl status patents-intake --no-pager
exit
```



## 6. Configure Component B (search)

Connect:

```bash
gcloud compute ssh patents-search \
  --project="$PROJECT_ID" --zone="$ZONE"
```

Edit:

```bash
sudoedit /etc/agentic-patents/search.env
```

Set:

```text
APP_ENV=gcp
GCP_PROJECT=YOUR_PROJECT_ID
PUBSUB_SEARCH_PLANS_TOPIC=search-plans
PUBSUB_SEARCH_PLANS_SUBSCRIPTION=search-plans-sub
PUBSUB_CANDIDATES_TOPIC=candidates
REDIS_HOST=REDIS_PRIVATE_IP
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_KEY
EXA_API_KEY=YOUR_EXA_KEY
```

Component B intentionally receives no Cloud SQL credentials.

```bash
sudo chmod 600 /etc/agentic-patents/search.env
sudo systemctl restart patents-search
sudo systemctl status patents-search --no-pager
exit
```



## 7. Configure Component C (report)

Connect:

```bash
gcloud compute ssh patents-report \
  --project="$PROJECT_ID" --zone="$ZONE"
```

Edit:

```bash
sudoedit /etc/agentic-patents/report.env
```

Set:

```text
APP_ENV=gcp
GCP_PROJECT=YOUR_PROJECT_ID
PUBSUB_SEARCH_PLANS_TOPIC=search-plans
PUBSUB_CANDIDATES_TOPIC=candidates
PUBSUB_CANDIDATES_SUBSCRIPTION=candidates-sub
CLOUD_SQL_DSN=postgresql://postgres:YOUR_DB_PASSWORD@CLOUD_SQL_PRIVATE_IP:5432/patents
REDIS_HOST=REDIS_PRIVATE_IP
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_KEY
EXA_API_KEY=YOUR_EXA_KEY
```

```bash
sudo chmod 600 /etc/agentic-patents/report.env
sudo systemctl restart patents-report
sudo systemctl status patents-report --no-pager
exit
```

Never commit these environment files or copy their contents into logs.

## 8. Check service logs

Run the relevant command on each VM:

```bash
sudo journalctl -u patents-intake -n 100 --no-pager
sudo journalctl -u patents-search -n 100 --no-pager
sudo journalctl -u patents-report -n 100 --no-pager
```

For live logs, replace `-n 100` with `-f`.

## 9. Open a secure tunnel to the intake API

From your local machine, keep this command running:

```bash
gcloud compute ssh patents-intake \
  --project="$PROJECT_ID" --zone="$ZONE" \
  -- -L 8000:localhost:8000
```

The API is then available only through the tunnel at
`http://localhost:8000`.

## 10. Submit and poll a job

In another local terminal:

```bash
curl -sS -X POST http://localhost:8000/jobs \
  -F "specification=@/absolute/path/to/specification.pdf;type=application/pdf" \
  -F "claims=@/absolute/path/to/claims.txt;type=text/plain" \
  -F "critical_date=2024-01-01"
```

Copy the returned `job_id`, then poll:

```bash
curl -sS "http://localhost:8000/jobs/JOB_ID"
```

Expected statuses are `analyzing`, `searching`, `ranking`, `completed`, or
`failed`. A completed response includes the report.

## 11. Deploy later code updates

Commit the new code, then run locally:

```bash
cd deploy
export PROJECT_ID="YOUR_PROJECT_ID"
export ZONE="us-central1-a"
./update.sh
```

The script archives one Git commit, uploads that exact version to every VM,
updates dependencies, and restarts all three services. It does not change the
protected environment files.

## 12. Stop billing

When the deployment is no longer needed:

```bash
cd deploy
export PROJECT_ID="YOUR_PROJECT_ID"
export REGION="us-central1"
export ZONE="us-central1-a"
./teardown.sh
```

This deletes the VMs, Cloud SQL, Memorystore, Pub/Sub resources, and service
accounts. It intentionally leaves the free VPC peering and reserved private
address range because other project resources may share them.

## Known deployment limitations

- The demo uses the built-in PostgreSQL `postgres` user for Components A and
C. Separate least-privilege database users remain follow-up work.
- Memorystore is configured without AUTH or in-transit encryption and is
reachable only over the private VPC.
- The VMs retain external IPs for outbound Anthropic, Exa, package-install,
and SSH traffic. The intake API still binds only to `127.0.0.1`.
- `update.sh` extracts over the existing release directory. Files deleted
from a later Git commit can remain on a VM; fresh release directories with
an atomic `current` symlink would make updates stricter.
- Pub/Sub poison messages are logged and dropped. Their jobs can remain
stranded until manually resubmitted, as documented in `spec.md`.

