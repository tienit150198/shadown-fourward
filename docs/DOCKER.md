# Docker deployment

Run SHADOW on an AMD64 or ARM64 Linux host with Docker Engine, the Compose plugin, Bash and curl 7.71 or later. The app runs on CPU and calls remote APIs for inference.

From the repository checkout on the server, run:

```bash
bash deploy/run.sh
```

Use `sudo bash deploy/run.sh` if your Docker installation requires elevated permissions. On first run, the script copies `.env.example` to `.env` with owner-only permissions. Fill in the following keys in `.env`, then rerun the same command.

| Key | Used for |
| --- | --- |
| `LLM_API_KEY` | Hypertek Qwen chat, vision and image generation at `https://llm.hypertek.vn/v1` |
| `OPENROUTER_API_KEY` | TypeSafe Jev intent routing via OpenRouter |
| `VIDU_API_KEY` | Vidu photo try-on and Vidu S2 Editing for live video try-on |

The template selects Hypertek and Qwen models for chat and vision, `SHADOW_INTENT_PROVIDER=jev` and `TRYON_PHOTO_PROVIDER=vidu`. Keep `VISION_MODEL` and `COMPILER_MODEL` set to model IDs available through Hypertek. Set `HYPERTEK_API_KEY` if Qwen Image uses a separate key. Vidu defaults to `https://api.vidu.com`. For accounts in the China region, set `VIDU_API_BASE_URL=https://api.vidu.cn`.

Put the launcher settings in `.env` so Compose passes the same values into the app. If a value contains a literal `$`, enclose it in single quotes. The file is loaded at runtime and excluded from the image.

The script builds and starts the containers, waits for Compose health checks, then checks `/api/health` for AI mode. The app listens on `127.0.0.1:8000` by default. `SHADOW_PORT` changes the host port.

## Public HTTPS

Set `SHADOW_DOMAIN=shadow.example.com` and a `SHADOW_ACCESS_CODE` in `.env`. Point the domain to the server and allow inbound TCP 80 and 443 in both the cloud and host firewalls. Run `bash deploy/run.sh` to start Caddy and check the HTTPS endpoint. Caddy obtains and renews the TLS certificate and forwards WebSockets. HTTPS is required for phone camera access.

If the HTTPS check fails after startup, inspect the Caddy logs and verify DNS and firewall settings. Clearing `SHADOW_DOMAIN` and rerunning the launcher removes the Caddy container from this Compose project.

## Operations and data

```bash
docker compose ps
docker compose logs --tail=100 -f app
bash deploy/run.sh                 # rebuild after code or configuration changes
docker compose down               # stop the stack and retain named volumes
```

When HTTPS is enabled, use `docker compose -f compose.yaml -f compose.https.yaml` for manual Compose operations, including `logs --tail=100 proxy` and `down`. Keep the app's published port bound to loopback and use trusted containers on its Docker network because the app trusts forwarded headers.

Skills, wardrobe images, rules, profiles and evaluations live in `shadow_skills`, `shadow_wardrobe` and `shadow_evals`. The launcher preserves existing `.env` and named volumes. New skills volumes receive the bundled sample skill. Host data outside Compose volumes requires manual migration. For a consistent backup, stop the app while copying the volumes. The HTTPS overlay stores certificates in `shadow_caddy_data`. `docker compose down -v` deletes the named volumes.

The app runs as a non-root user with one worker. Active sessions, background jobs and rate limits are in memory and reset when the app restarts. Dependencies follow the repository's version ranges, so rebuilds may resolve newer releases.

SHADOW ends browser try-on sessions after 120 seconds by default. The provider applies its own maximum duration and stops billing on hangup or when its reconnect grace period expires. See the [Vidu realtime API](https://platform.vidu.com/vidu-stream/doc/s2-editing/realtime/parameters).

## Offline demo

For an offline demo, copy `.env.example` to `.env` if needed, leave `LLM_API_KEY` empty and set `SHADOW_MODE=mock` and `TRYON_PHOTO_PROVIDER=mock`. Start the loopback stack directly with `docker compose up -d --build --wait`.

For an existing CatVTON worker, start Compose directly with `TRYON_PHOTO_PROVIDER=worker` and a private VPN address for `TRYON_WORKER_URL`. Configure `TRYON_TOKEN` on both ends if the worker requires it.

## CI and owner-controlled deployment

[The CI and deployment workflow](../.github/workflows/ci.yml) runs backend tests
for pull requests and pushes to `main`. Deployment is manual: open **Actions →
CI and deployment → Run workflow**, select `main` and your deployment environment.
After tests pass, it builds a Linux ARM64 image and deploys through the restricted
rootless SSH account. Any environment approval rules apply before deployment.
Pushes and pull requests never receive deployment credentials or deploy automatically.

For the current production server, follow [rootless CI setup and operations](ROOTLESS_CI.md).
The rootful Docker daemon is disabled; use those operations commands instead of
this document's generic `deploy/run.sh` instructions on the migrated server.
Do not register production as a self-hosted runner for the shared repository.
