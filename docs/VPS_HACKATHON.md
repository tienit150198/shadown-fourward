# Hackathon VPS deployment

Host: `103.195.238.178`, application: `hackathon.trioxstudios.com`.
The existing system Nginx handles ports 80/443 for several apps. SHADOW uses a
separate Docker Compose project, `shadow-hackathon`, with only
`127.0.0.1:18080` published. Do not start the Caddy HTTPS overlay on this host.

## Paths and state

- Current app: `/opt/shadow/current` → `/opt/shadow/releases/<commit>`.
  Use `readlink -f /opt/shadow/current` to identify the active release.
- Backend source commit: recorded in `/opt/shadow/deployed-commit` after a healthy deployment.
- Environment: `/opt/shadow/.env`, mode 0600, linked into the release directory.
  Includes `SHADOW_PORT=18080`; AI keys stay in this file.
- Persistent volumes: `shadow-hackathon_skills`, `shadow-hackathon_wardrobe`,
  `shadow-hackathon_evals`. The wardrobe was initialized with the existing 262
  catalog items and images, not past try-on photos.
- Nginx site: `/etc/nginx/sites-available/shadow-hackathon`, enabled by symlink.
- ACME webroot: `/var/www/shadow-acme`.
- HTTPS setup files: `/opt/shadow/deploy/` (the two files under `deploy/` in this repo).

Build sources are transferred as a Git archive over SSH. GitHub authentication is
kept on the workstation; no GitHub token is stored on the VPS. A push is verified
separately against the remote branch's commit.

## DNS and HTTPS

Authoritative DNS for `trioxstudios.com` is Cloudflare (`elaine`/`karl`), not ZoneDNS.
Add **A / hackathon / 103.195.238.178 / DNS only / Auto TTL** to that zone.
Do not change the zone's nameservers or unrelated records.

After DNS resolves, run:

```sh
cd /opt/shadow/deploy
./finish-hackathon-https.sh
curl --fail https://hackathon.trioxstudios.com/api/health
```

This obtains a certificate through the existing Certbot installation/account,
installs `nginx-hackathon.conf`, checks Nginx before reloading, and configures
certificate renewal to reload Nginx. An invalid Nginx config is restored before
any reload. The initial site serves HTTP so ACME and deployment checks work while
DNS is pending; the HTTPS config redirects HTTP afterwards.

## Operation

```sh
cd /opt/shadow/current
docker compose -p shadow-hackathon ps
docker compose -p shadow-hackathon logs --tail=100 app
docker compose -p shadow-hackathon restart app
```

Back up the named volumes before updates. Keep `.env` shared between release
directories. Never run `docker compose down -v` against the production project.
Deploy future releases with the same Compose project name to retain the volumes.

## Verified 2026-11-14

Cloudflare DNS now resolves to `103.195.238.178`. HTTPS is live at
`https://hackathon.trioxstudios.com`, with HTTP redirected to HTTPS. The certificate
expires on 2027-02-11; `certbot.timer` is active and renewal reloads Nginx.
The Nginx TLS listener uses HTTP/2 consistently with the host's other sites.

Container healthy; public HTTPS passes health, 262-item wardrobe, image and
web-page checks with normal DNS and certificate validation. Streaming conversation
returns real wardrobe outfits for summer, 15°C/rain and tomorrow's GPS forecast.
The public WSS connection upgrades and receives `session.ready`. The AI
integrations are configured, but no paid voice/try-on session was created during
deployment verification.
