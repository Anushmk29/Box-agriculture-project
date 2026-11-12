#!/usr/bin/env bash
# Run from the repository root. Uses only disposable fixture data, never Supabase.
set -Eeuo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
mkdir -p test-results
web_pid=
api_pid=
cleanup() {
    if [[ -n $api_pid ]]; then kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true; fi
    if [[ -n $web_pid ]]; then kill "$web_pid" 2>/dev/null || true; wait "$web_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT

wait_for() {
    local url=$1 pid=$2
    for ((attempt=0; attempt<90; attempt++)); do
        kill -0 "$pid" 2>/dev/null || { printf 'Server exited before ready: %s\n' "$url" >&2; return 1; }
        if curl --fail --silent --output /dev/null --max-time 2 "$url"; then return 0; fi
        sleep 1
    done
    printf 'Server did not become ready: %s\n' "$url" >&2
    return 1
}

# Build web first with BACKEND_ORIGIN=http://127.0.0.1:8106.
node web/node_modules/next/dist/bin/next start web --hostname 127.0.0.1 --port 3000 >test-results/web.log 2>&1 &
web_pid=$!
wait_for http://127.0.0.1:3000/login "$web_pid"

# Each spec gets a fresh fixture process/database so mutations cannot leak
# between the real workflow suites. No production credentials are provided.
for spec in release-smoke.spec.ts browser-project.spec.ts client-journey-live.spec.ts language.spec.ts release-readiness.spec.ts; do
    (
        cd backend
        exec .venv/bin/python -m uvicorn fixtures.resilience_browser_api:app --app-dir tests --host 127.0.0.1 --port 8106
    ) >"test-results/api-$spec.log" 2>&1 &
    api_pid=$!
    wait_for http://127.0.0.1:8106/qa-fixtures "$api_pid"
    (
        cd web
        QA_API_URL=http://127.0.0.1:8106 E2E_BASE_URL=http://127.0.0.1:3000 \
            PLAYWRIGHT_JUNIT_OUTPUT_NAME="${spec%.spec.ts}.xml" \
            npx playwright test "$spec" --workers=1 --reporter=line,junit
    )
    kill "$api_pid"
    wait "$api_pid" || true
    api_pid=
done
