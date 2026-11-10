# Docker deployment

The default stack runs the app on `127.0.0.1:8000`. It works on this
ARM64 server and on AMD64. The AI try-on worker runs separately.

```bash
cp .env.example .env  # first setup only; preserve an existing .env
chmod 600 .env
sudo docker compose up -d --build --wait
curl http://127.0.0.1:8000/api/health
```

Without an LLM key the app uses mock mode. Set the gateway URL, key and
models in `.env` for AI mode. Set `SHADOW_ACCESS_CODE` before public access.
Optional `SHADOW_PORT` changes the host loopback port.

## Public HTTPS

Set `SHADOW_DOMAIN=shadow.example.com` in `.env`, point that domain's DNS
to this server, and allow inbound TCP 80/443 in the cloud and host firewalls.
Caddy obtains and renews the certificate and forwards WebSockets. HTTPS
is required for phone camera access.

```bash
sudo docker compose -f compose.yaml -f compose.https.yaml up -d --build --wait
```

Use the same two `-f` flags for subsequent commands when running HTTPS.
The app trusts forwarded headers because its published port is loopback-only;
keep that binding private and only use trusted containers on its Docker network.

## Operations and data

```bash
sudo docker compose ps
sudo docker compose logs --tail=100 -f app
sudo docker compose up -d --build --wait  # rebuild after code/config changes
sudo docker compose down               # retains named volumes
```

Skills, wardrobe images/rules/profile and evaluations live in named volumes
`shadow_skills`, `shadow_wardrobe`, and `shadow_evals`. New skills volumes
receive the bundled sample skill on first creation. Existing host data is not
automatically migrated. Back up all three volumes before upgrades; stop the app
while taking a consistent backup. Do not use `down -v` unless deleting data is intended.
The HTTPS overlay also retains certificates in `shadow_caddy_data`.

`.env` is passed at runtime and excluded from the image. Restart/recreate the
service after changing it. The app runs as a non-root user with one worker:
sessions, background jobs and rate limits are in memory and reset on restart.
Dependencies currently follow the repository's version ranges; rebuilds may
resolve newer releases.

For Mac mini try-on, use a reachable private VPN address for `TRYON_WORKER_URL`
and configure `TRYON_TOKEN` on both ends. A home LAN address is not reachable
from this server by default. No model weights are installed in the app image.
