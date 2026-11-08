#!/usr/bin/env bash
# Run the script from a release bundle already copied to ROOT/releases/ID.
set -Eeuo pipefail
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# -ge 1 && $# -le 2 ]] || fail "Usage: bash deploy.sh /opt/fresh-produce-ENV [--rollback]"
mode=${2:-deploy}
[[ $mode == deploy || $mode == --rollback ]] || fail "Unknown deployment mode"
for tool in docker flock curl realpath; do
    command -v "$tool" >/dev/null || fail "Missing required command: $tool"
done

root=$(realpath -e -- "$1")
bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $bundle == "$root"/releases/* ]] || fail "Bundle must be inside $root/releases/"
[[ -f $root/.env && -f $root/app.env && -f $bundle/release.env ]] || fail "Missing .env, app.env or release.env; see README.md"
# The checked files, not a caller's exported shell variables, select this release.
unset COMPOSE_PROJECT_NAME API_IMAGE WEB_IMAGE APP_DOMAIN ACME_EMAIL CADDY_IMAGE

# One release at a time, including deployments started manually on the VPS.
exec 9>"$root/.deploy.lock"
flock -n 9 || fail "Another deployment is running"

release=$(sed -n 's/^RELEASE_ID=//p' "$bundle/release.env")
[[ $release =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}$ ]] || fail "Invalid RELEASE_ID"
[[ $(basename -- "$bundle") == "$release" ]] || fail "Bundle directory must match RELEASE_ID"
for key in API_IMAGE WEB_IMAGE; do
    value=$(sed -n "s/^${key}=//p" "$bundle/release.env")
    [[ $value =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || fail "$key must be a GHCR image pinned by digest"
done
domain=$(sed -n 's/^APP_DOMAIN=//p' "$root/.env")
[[ $domain =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && $domain == *.* ]] || fail "APP_DOMAIN must be a DNS hostname"
[[ ! $domain =~ ^[0-9.]+$ ]] || fail "Use a public DNS hostname such as your Hostinger hostname, not a bare IP address"
if grep -Eq 'REPLACE_|PROJECT_REF|POOLER_HOST|URL_ENCODED_PASSWORD' "$root/app.env"; then
    fail "Replace the app.env placeholders before deployment"
fi

export APP_ENV_FILE="$root/app.env"
dc() {
    docker compose --env-file "$root/.env" --env-file "$bundle/release.env" -f "$bundle/compose.yml" "$@"
}
# Do not print `compose config`: it contains resolved application secrets.
dc config --quiet
docker info >/dev/null
previous=
if [[ -L $root/current && -d $root/current ]]; then
    previous=$(readlink -f -- "$root/current")
    [[ $previous == "$root"/releases/* ]] || fail "Current bundle points outside this deployment root"
fi
if [[ $mode == --rollback ]]; then
    [[ -n $previous ]] || fail "No previous deployment recorded; rollback is not an initial deployment"
    printf '%s\n' 'Restoring application images only. Database schema will not be downgraded.'
fi

on_failure() {
    printf '%s\n' "Release $release failed. A migration may already have committed." >&2
    printf '%s\n' 'Inspect container logs. Do not run alembic downgrade automatically.' >&2
    if [[ -n $previous ]]; then
        printf 'Last successful bundle: %s\n' "$previous" >&2
        printf 'If schema-compatible, restore it with: bash %q/deploy.sh %q --rollback\n' "$previous" "$root" >&2
    fi
}
trap on_failure ERR

dc pull api web proxy
if [[ $mode != --rollback ]]; then
    dc --profile tools run --rm --no-deps migrate
fi
dc up -d --wait --wait-timeout 180 api web proxy

# Check real DNS, TLS, the public API route, and the frontend after startup.
for path in /backend/ready /login; do
    curl --fail --silent --show-error --output /dev/null \
        --retry 12 --retry-all-errors --retry-delay 5 --max-time 10 \
        "https://$domain$path"
done

if [[ -n $previous && $previous != "$bundle" ]]; then
    ln -sfnT -- "$previous" "$root/previous"
fi
ln -sfnT -- "$bundle" "$root/current"
printf 'Release %s is ready at https://%s\n' "$release" "$domain"
