# Prior Art frontend

Local UI for the intake API. The API has no CORS headers, so this server
serves the page and proxies `/jobs` to the SSH tunnel on port 8000.

1. Keep the intake tunnel open (`gcloud compute ssh ... -L 8000:localhost:8000`).
2. From the repo root:

```bash
python Frontend/server.py
```

3. Open [http://127.0.0.1:3000](http://127.0.0.1:3000).
