# Deploy Fresh Produce on a Hostinger VPS

Read [the deployment plan](PLAN.md) first. Use this bundle for a **Linux x86-64
VPS**, not shared web hosting. Keep Supabase as the database/private file store.
No Kubernetes or additional local database/Redis service is required by this stack.

## Files

| File | Purpose |
| --- | --- |
| `compose.yml` | Caddy, web, API, and an explicit migration job |
| `Caddyfile` | Automatic HTTPS and same-origin API/upload routing |
| `vps.env.example` | Domain, certificate contact and Compose project name |
| `app.env.example` | Server-only application and Supabase configuration |
| `release.env.example` | Image-digest manifest format; CI writes the real file |
| `deploy.sh` | Serialized pull, migration, startup, public checks and release record |
| `../../.github/workflows/ci.yml` | PR/main tests, image checks, release publishing |
| `../../.github/workflows/deploy-vps.yml` | Deploy one successful CI artifact over SSH |

## 1. Prepare Hostinger

Your existing **Hostinger KVM 2: Ubuntu 24.04 LTS, 2 CPU cores, 8 GB RAM,
100 GB disk** is a suitable starting server. Keep this OS; install Docker
Engine/Compose if missing. Build images in CI and measure real staff concurrency
before changing the VPS size.

From the VPS terminal, check prerequisites:

```bash
docker version
docker compose version
command -v bash curl flock realpath
```

Use Compose **2.30 or newer**, including `env_file: format: raw` and `up --wait`.
Install missing utilities using Ubuntu's package manager. Do not replace a working
server OS or reset its firewall just to use this bundle.

Create a dedicated deployment account and root directory, as a server administrator:

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -o deploy -g deploy -m 700 /home/deploy/.ssh
install -d -o deploy -g deploy -m 750 /opt/fresh-produce-production
install -d -o deploy -g deploy -m 750 /opt/fresh-produce-production/releases
```

Install your administration and CI public SSH keys in the account's
`~/.ssh/authorized_keys`, owned by `deploy`, mode `600`. Docker group membership
grants control of the host; reserve this account for trusted deployments.
Reconnect before testing group access. Confirm key login before disabling password
login. Record the SSH host-key fingerprint through the Hostinger console.

In Hostinger's VPS firewall and any host firewall, allow TCP 80/443 and your SSH
port. The CI runner must be able to reach SSH; a rule allowing only your home IP
will block GitHub-hosted runners. Use an appropriate trusted runner/network if you
require fixed-source SSH access. Do not publish ports 3000, 8000, or PostgreSQL.

You do not need to buy a domain for this initial deployment. Your Hostinger
hostname is **srv1878510.hstgr.cloud**, and the supplied environment templates
now use `https://srv1878510.hstgr.cloud` for the application origin. Public DNS
resolved it on 1 November 2026 to `200.141.12.18` (IPv4) and
`2a02:4780:63:a71d::1` (IPv6). Compare these with hPanel and ensure ports 80/443
reach this VPS over the published address families. Resolving DNS does not verify
firewall reachability or guarantee successful certificate issuance.

Ensure no existing Nginx/Apache/Caddy or another Compose project owns ports
80/443. Caddy will request and renew a publicly trusted certificate for the
hostname when DNS and reachability are correct. Production requires HTTPS for
secure login; entering just the numeric IP or bypassing certificate errors is not
the deployment mode supported by this bundle. A custom domain can be added later
by updating `APP_DOMAIN`, `PUBLIC_APP_URL` and `CORS_ORIGINS`, then redeploying.

This configuration runs **one environment at a time per VPS**. For your initial
rehearsal, staging may use this same server and hostname, with separate Supabase
credentials, `/opt/fresh-produce-staging` and `COMPOSE_PROJECT_NAME=fresh-produce-staging`.
Create its directories as above, configure its own env files, and set the GitHub
staging environment to that root. Stop staging before starting production:

```bash
APP_ENV_FILE=/opt/fresh-produce-staging/app.env docker compose \
  --env-file /opt/fresh-produce-staging/.env \
  --env-file /opt/fresh-produce-staging/current/release.env \
  -f /opt/fresh-produce-staging/current/compose.yml stop proxy web api
```

This preserves staging data and certificates while freeing ports 80/443. Once
production is live, use a separate machine for staging or adapt the shared proxy
before running both concurrently. Do not stop production to run routine CI tests.

## 2. Configure the server

As `deploy`, copy the templates into the deployment root and edit them privately:

```bash
cp vps.env.example /opt/fresh-produce-production/.env
cp app.env.example /opt/fresh-produce-production/app.env
chmod 600 /opt/fresh-produce-production/.env /opt/fresh-produce-production/app.env
```

The templates are in the repository and the CI release artifact. Set the actual
hostname, certificate email, and unique Compose project name in `.env`.
Set `PUBLIC_APP_URL` and `CORS_ORIGINS` to that same HTTPS origin in `app.env`.

Use the intended Supabase project's session pooler URI on port 5432 with
`sslmode=require`; URL-encode its password. Configure the same project's private
`order-sources` bucket and server-only Storage key. Do not put Supabase secrets in
the frontend, a `NEXT_PUBLIC_*` variable, GitHub artifacts, or the repository.
Keep Storage garbage collection disabled during cutover and recovery checks.

`app.env` is read as **raw environment values**: do not add shell quoting or source
it as a script. JSON such as `CORS_ORIGINS=["https://app.example.com"]` stays literal;
`$` in a credential is not expanded. Do not print `docker compose config` because
its resolved output includes secrets; use `config --quiet` for validation.

Preserve the existing strong production `SECRET_KEY` when moving an existing
installation. Rotating it logs users out and can affect legacy password hashes.
For a genuinely new installation, generate a random key with at least 32 characters.
Keep seed credentials and reset scripts out of automatic releases; existing data
and customer mappings must be preserved. Set all invoice bank fields together only
when approved. Retain the current reviewed feature-flag settings.

Authorize this VPS account to pull the two private GHCR images once:

```bash
# Enter the read-only packages token at the prompt; do not include it in a command.
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

Use a token with `read:packages` and package access. Verify organization/SSO access
if applicable. This credential stays in the deployment account's Docker config;
the CI runner's short-lived publishing token is not copied to the VPS.

## 3. Configure GitHub

Create `staging` and `production` repository environments. Set these independently
for each environment:

| Kind | Name | Example / contents |
| --- | --- | --- |
| Variable | `VPS_HOST` | VPS IPv4 address or SSH hostname |
| Variable | `VPS_PORT` | `22`, or your configured SSH port |
| Variable | `VPS_USER` | `deploy` |
| Variable | `VPS_ROOT` | `/opt/fresh-produce-production` or `/opt/fresh-produce-staging` |
| Secret | `VPS_SSH_KEY` | Dedicated CI private SSH key |
| Secret | `VPS_KNOWN_HOSTS` | Full OpenSSH known_hosts entry verified against the console fingerprint |

Do not generate trust by blindly running `ssh-keyscan` inside every deployment.
For a non-default port, the known_hosts entry uses `[hostname]:port`. Require SSH
key authentication and keep the workflow's strict host-key checking enabled.

The CI workflow needs permission to publish packages with `GITHUB_TOKEN`. Grant
the repository access to existing packages if GHCR reports a permissions error.
Require the backend, web, and images jobs on pull requests before merging to `main`.
Configure a production environment reviewer if your GitHub plan supports it;
otherwise keep production deployment as an authorized manual workflow dispatch.

## 4. CI and deployment

Every PR and push to `main` runs:

1. Locked backend dependencies, Ruff, an empty PostgreSQL Alembic upgrade, all
   backend tests, and PostgreSQL races against a disposable local `box_tests` DB.
2. `npm ci`, ESLint, frontend unit tests, a production build, TypeScript checks,
   and Playwright Chromium against the disposable fixture API. Selected suites are
   `release-smoke`, `browser-project`, `client-journey-live`, `language`, and `release-readiness`; the full historical
   browser corpus and hosted/live account audits are not part of this initial gate.
3. Both production image builds, backend/frontend container checks, Caddy config
   validation, and deployment failure/rollback sequencing tests.

Only a successful **push to `main`** publishes private GHCR images and a
`vps-release` artifact. Images use unique commit/run tags, and the manifest records
immutable digests. The frontend is built with `BACKEND_ORIGIN=http://api:8000`,
which is valid in both environments, so promotion does not rebuild the app.

To deploy, open **Actions → Deploy Hostinger VPS → Run workflow** from `main`,
choose `staging`, and enter the successful **CI run ID** from its URL. The workflow
verifies that CI passed for a push to this repository's `main`, downloads its
release artifact, and copies it over authenticated SSH.

On the VPS, `deploy.sh`:

1. Takes an exclusive deployment lock and validates configuration/image digests.
2. Pulls the tested images; runs `alembic upgrade head` in a one-off container.
3. Replaces the services and waits for database/Storage and frontend health.
4. Checks public HTTPS `/backend/ready` and `/login` without disabling TLS checks.
5. Updates `current` and `previous` bundle symlinks only after success.

Before production, verify staging's real login/logout, upload/source retrieval,
procurement/packing/final-invoice workflow, hardware printing/scanning, concurrent
edits, backups and recovery. Then select **production and the same CI run ID**.
This staging acceptance is an operator responsibility, not an automatically checked
condition in the deployment workflow. The post-deploy smoke check does not perform
authenticated business mutations against production.

## 5. Manual deployment and rollback

If deploying without the SSH workflow, download the successful CI's `vps-release`
artifact and read `RELEASE_ID` from `release.env`. Copy its contents into
`/opt/fresh-produce-production/releases/RELEASE_ID/`, then run:

```bash
bash /opt/fresh-produce-production/releases/RELEASE_ID/deploy.sh /opt/fresh-produce-production
```

Never substitute untested `latest` tags. Keep release artifacts and both package
digests for your chosen rollback retention period; artifact retention is 90 days.

For a **schema-compatible application rollback**, select the last successful
bundle. If the new deployment succeeded, it is normally the `previous` symlink:

```bash
bash /opt/fresh-produce-production/previous/deploy.sh /opt/fresh-produce-production --rollback
```

If a deployment failed, `current` still identifies the last successful bundle:

```bash
bash /opt/fresh-produce-production/current/deploy.sh /opt/fresh-produce-production --rollback
```

Rollback skips migrations and does not undo database changes or restore lost data.
Use additive/backward-compatible migrations, and rehearse compatibility before
release. A failure after migration or service replacement can leave some new code
running: the failed workflow is a signal to inspect and explicitly recover, not a
promise of automatic rollback. Do not run `alembic downgrade` against business data
as an automated recovery step. Single-host replacement can briefly interrupt traffic.

## 6. Operations

Keep an external uptime check on `/backend/ready` and `/login`; add error alerts,
disk/memory monitoring, and scheduled OS/image maintenance. Docker health checks
detect failures; `restart: unless-stopped` restarts exited processes, not every
unhealthy process. Assign an operator to investigate sustained failures.

Database and private Storage backups are separate from VPS snapshots. Schedule
encrypted off-host copies and retention, verify file hashes and database/source
references, and restore to an isolated target to measure recovery time. Copy any
remaining legacy source documents before retiring the old host. Do not delete
Caddy volumes or known-good images/bundles during routine cleanup.

For service status/logs, use the current release's Compose file and the same env
files as `deploy.sh`, keeping output private. Be aware logs can contain business
data. Docker logs are capped at three 10 MB files per service in this bundle.

## Verification status

See [the release readiness record](RELEASE_READINESS.md) for checks performed on
8 November 2026, known master-data gaps and the remaining staging prerequisites.
Use [the staff acceptance script](STAFF_PILOT.md) before production.
Local checks do not establish hosted deployment or human acceptance.

## References

- [Hostinger Docker VPS template](https://support.hostinger.com/en/articles/8306612-how-to-use-the-docker-vps-template)
- [Hostinger Docker Manager](https://www.hostinger.com/support/12040789-hostinger-docker-manager-for-vps-simplify-your-container-deployments/)
- [Hostinger-provided Docker hostnames](https://www.hostinger.com/support/how-to-change-the-domain-of-a-docker-project/)
- [Docker Compose startup/readiness](https://docs.docker.com/compose/how-tos/startup-order/)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [GitHub deployment environments and plan limits](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
