#!/usr/bin/env bash
# Start both services on loopback, run migrate-guard against them, and stop them.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"
mkdir -p "$here/.data"
export LEGACY_DB="$here/.data/legacy.db" REWRITE_DB="$here/.data/rewrite.db"

uv run --group demo flask --app "$here/legacy/app.py" run -p 5001 >/dev/null 2>&1 &
legacy=$!
uv run --group demo uvicorn main:app --app-dir "$here/rewrite" --port 5002 --log-level warning &
rewrite=$!
trap 'kill $legacy $rewrite 2>/dev/null || true' EXIT

for port in 5001 5002; do
  for _ in $(seq 50); do
    if curl -s -o /dev/null "http://127.0.0.1:$port/"; then break; fi
    sleep 0.1
  done
done

uv run migrate-guard run --config "$here/migrate-guard.yaml" "$@"
