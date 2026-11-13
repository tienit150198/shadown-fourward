# This server: rootless Docker and restricted CI deployment

The production app runs under the unprivileged `shadowdeploy` account. The
rootful Docker service and socket are disabled. The source checkout stays at
`/home/ubuntu/shadown-fourward`; production uses private runtime files in
`/var/lib/shadowdeploy/deployment`. No shared source checkout is required.

The CI SSH public key is installed in the root-owned file
`/etc/ssh/authorized_keys/shadowdeploy`. SSH is restricted by
`/etc/ssh/sshd_config.d/00-shadowdeploy.conf` to the root-owned program
`/usr/local/libexec/shadow-ci-deploy`. The account has no sudo permission.
Interactive shells, arbitrary commands, SCP/SFTP, forwarding, and user SSH rc
files are disabled. Only `status` and `deploy <40 lowercase hex commit SHA>`
are accepted. The deployment command receives a Docker image archive on stdin.

## CI configuration

Keep the private key on your computer until you add its entire contents to the
owner-controlled deployment workflow's `DEPLOY_SSH_KEY` secret. Do not paste it
in chat or commit it. Use `DEPLOY_HOST=fourward.thangqt.com` and
`DEPLOY_USER=shadowdeploy`, with SSH port 22.

The workflow uses the GitHub environment you select when running it (default:
`production`). Add `DEPLOY_SSH_KEY` there; optional `DEPLOY_HOST` can be an
environment variable or secret, defaulting to `fourward.thangqt.com`. The SSH
username is fixed to `shadowdeploy`. Configure required approval and allowed
branches in the environment as supported by your GitHub plan.

To release, open **Actions → CI and deployment → Run workflow**, select `main`
and your existing environment, then approve the deployment if requested.
The workflow tests the selected commit, builds an ARM64 image on a GitHub-hosted
runner, and uploads that exact image to production. It pins the server host key,
serializes production deploys, and checks SSH status and public HTTPS afterward.
Deploying from other branches is disabled; pushes and pull requests only test.
The workflow does not copy source or application secrets to/from the server.

Anyone allowed to deploy an image can run application code and therefore access
application runtime secrets. Restrict environment access accordingly. A separate
owner-controlled deployment repository remains an option for stronger separation.

Build the image for this ARM64 server, using a full reviewed commit SHA:

```bash
set -euo pipefail
RELEASE_SHA=$(git rev-parse HEAD)
docker buildx build --platform linux/arm64 --load \
  -t "shadow-app:ci-$RELEASE_SHA" .

# CI writes DEPLOY_SSH_KEY to a temporary key file with mode 600, and installs
# the pinned server host key from the owner-provided known_hosts value below.
docker image save "shadow-app:ci-$RELEASE_SHA" |
  ssh -T -i "$DEPLOY_KEY_FILE" \
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    "$DEPLOY_USER@$DEPLOY_HOST" "deploy $RELEASE_SHA"
```

Check status with the same SSH options and the command `status`.
Do not disable host-key verification or trust an unverified `ssh-keyscan` result.

Uploads must be uncompressed `docker image save` archives, at most 1 GiB, with
exactly one tagged Linux ARM64 image named `shadow-app:ci-<SHA>`. Deployments are
serialized, limited to 15 minutes, and use a fixed Compose configuration. The
server pins both app and proxy images by immutable ID. CI cannot replace the
Compose files, mounts, port mappings, or `.env` through this SSH interface.
Failed app startup or health checks restore the previous image. Runtime data
changes made by application code are not rolled back.

## Owner operations

Use the dedicated account rather than `sudo docker` or the checkout's original
`deploy/run.sh` on this migrated server:

```bash
sudo -H -u shadowdeploy env XDG_RUNTIME_DIR=/run/user/1002 \
  docker compose --project-directory /var/lib/shadowdeploy/deployment \
  -f /var/lib/shadowdeploy/deployment/compose.yaml \
  -f /var/lib/shadowdeploy/deployment/compose.https.yaml ps
```

Edit production settings in `/var/lib/shadowdeploy/deployment/.env` as the owner;
the checkout's `.env` is no longer the live runtime configuration. Recreate the
app after changing runtime settings using the same Compose command with
`up -d --no-build --wait` instead of `ps`.

Rootless Docker is enabled in shadowdeploy's user systemd manager, with lingering
enabled so it starts at boot. RootlessKit has `cap_net_bind_service` for ports
80/443; recheck this capability after package upgrades. The persisted host
firewall permits TCP 80/443. The app's port 8000 remains bound to loopback.

Volume snapshots from the migration are at
`/var/backups/shadow-rootful-20261113T095015Z` (root-only). The earlier unused
shared checkout is archived there. Original rootful containers and volumes were
retained, with Docker disabled, for rollback. They are a point-in-time copy and
do not receive subsequent production writes.

Emergency rollback requires stopping the rootless stack before enabling
`docker.socket` and `docker.service`, then starting `shadow-app-1` and
`shadow-proxy-1` through rootful Docker. Restore newer runtime data first if
necessary. Never let both stacks contend for ports 80/443.

## Pinned SSH host key

Use this verified server key as the `known_hosts` entry in CI:

```text
fourward.thangqt.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJwEEfrDj8PVK2rwGwEciTtggqEdFfiqvum31X0Hfs1h
```
