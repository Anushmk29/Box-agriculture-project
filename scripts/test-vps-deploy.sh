#!/usr/bin/env bash
# Deployment sequencing/failure tests. Docker and HTTP are simulated; no VPS/DB.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
scratch=$(mktemp -d -t vps-deploy-tests.XXXXXXXX)
cleanup() {
    # Only delete the exact temporary test directory created above.
    [[ $(basename -- "$scratch") == vps-deploy-tests.* && -d $scratch ]] || return
    rm -rf -- "$scratch"
}
trap cleanup EXIT
mkdir -p "$scratch/bin" "$scratch/vps root/releases"
export COMMAND_LOG="$scratch/commands.log"
export PATH="$scratch/bin:$PATH"
cat > "$scratch/bin/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$COMMAND_LOG"
if [[ $* == *'run --rm --no-deps migrate'* && ${MOCK_MIGRATION_FAIL:-0} == 1 ]]; then exit 1; fi
SH
cat > "$scratch/bin/curl" <<'SH'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
if [[ ${MOCK_SMOKE_FAIL:-0} == 1 ]]; then exit 22; fi
SH
# Run on Linux (or WSL) with real flock and directory symlink support.
command -v flock >/dev/null
chmod +x "$scratch/bin/"*
root="$scratch/vps root"
printf 'COMPOSE_PROJECT_NAME=fixture\nAPP_DOMAIN=app.example.com\nACME_EMAIL=ops@example.com\n' > "$root/.env"
printf 'ENVIRONMENT=production\n' > "$root/app.env"
digest=$(printf '%064d' 0)
for release in release-a release-b; do
    mkdir "$root/releases/$release"
    cp "$repo/deploy/hostinger/"{deploy.sh,compose.yml,Caddyfile} "$root/releases/$release/"
    printf 'RELEASE_ID=%s\nAPI_IMAGE=ghcr.io/test/api@sha256:%s\nWEB_IMAGE=ghcr.io/test/web@sha256:%s\n' \
        "$release" "$digest" "$digest" > "$root/releases/$release/release.env"
done

bash "$root/releases/release-a/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1
[[ $(readlink "$root/current") == "$root/releases/release-a" ]]
[[ ! -L $root/previous ]]
grep -q 'run --rm --no-deps migrate' "$COMMAND_LOG"
grep -q 'up -d --wait' "$COMMAND_LOG"
grep -q 'https://app.example.com/backend/ready' "$COMMAND_LOG"

: > "$COMMAND_LOG"
if MOCK_MIGRATION_FAIL=1 bash "$root/releases/release-b/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1; then
    printf 'Migration failure incorrectly passed\n' >&2; exit 1
fi
[[ $(readlink "$root/current") == "$root/releases/release-a" ]]
! grep -q 'up -d --wait' "$COMMAND_LOG"

bash "$root/releases/release-b/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1
[[ $(readlink "$root/previous") == "$root/releases/release-a" ]]
: > "$COMMAND_LOG"
bash "$root/releases/release-a/deploy.sh" "$root" --rollback > "$scratch/deploy.log" 2>&1
! grep -q 'run --rm --no-deps migrate' "$COMMAND_LOG"
[[ $(readlink "$root/current") == "$root/releases/release-a" ]]
[[ $(readlink "$root/previous") == "$root/releases/release-b" ]]

if MOCK_SMOKE_FAIL=1 bash "$root/releases/release-b/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1; then
    printf 'HTTPS failure incorrectly passed\n' >&2; exit 1
fi
[[ $(readlink "$root/current") == "$root/releases/release-a" ]]

# A Hostinger-provided hostname works without purchasing a custom domain.
printf 'COMPOSE_PROJECT_NAME=fixture\nAPP_DOMAIN=srv123456.hstgr.cloud\nACME_EMAIL=ops@example.com\n' > "$root/.env"
: > "$COMMAND_LOG"
bash "$root/releases/release-a/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1
grep -q 'https://srv123456.hstgr.cloud/backend/ready' "$COMMAND_LOG"

# This bundle expects public DNS/TLS, not Caddy's default private-CA IP mode.
printf 'COMPOSE_PROJECT_NAME=fixture\nAPP_DOMAIN=203.0.113.10\nACME_EMAIL=ops@example.com\n' > "$root/.env"
: > "$COMMAND_LOG"
if bash "$root/releases/release-a/deploy.sh" "$root" > "$scratch/deploy.log" 2>&1; then
    printf 'Bare IP incorrectly accepted\n' >&2; exit 1
fi
[[ ! -s $COMMAND_LOG ]]
printf 'Deployment sequencing passed: first release, migration failure, promotion, application rollback, HTTPS failure, Hostinger hostname, bare-IP rejection.\n'
