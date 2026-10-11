#!/usr/bin/env bash
#
# Clone the production database into the local replica stack (compose.local.yml)
# and sanitize it so the local instance cannot reach any external service.
#
# Usage:
#   PROD_SSH=root@your-hetzner-host ./scripts/clone-prod.sh
#
# Optional env:
#   PROD_COMPOSE_DIR  directory holding compose.yml on the server (default /opt/ai-trainer)
#   PROD_DB_USER      postgres role on the server                 (default aitrainer)
#   PROD_DB           database name on the server                 (default aitrainer)
#   LOCAL_PASSWORD    password to set for local login             (default localdev)
#   KEEP_DUMP=1       keep the dump file instead of deleting it
#
# What the sanitize step does and why:
#   - clears strava_tokens / intervals_tokens: those columns are Fernet-encrypted
#     with STRAVA_ENCRYPTION_KEY, which is deliberately not present locally.
#     Deleting them turns an undecryptable-value error into a clean "not connected".
#   - clears users.user_openai_api_key / user_gemini_api_key for the same reason,
#     so local AI calls fall back to the keys in compose.local.yml.
#   - disables per-user auto-sync so the in-process scheduler (main.py:37-40,
#     which registers its jobs unconditionally) finds nothing to do.
#   - resets hashed_password: prod is Authelia-backed, so the DB only holds a
#     random throwaway hash (auth_router.py:105,185) and you could not log in.

set -euo pipefail

PROD_SSH="${PROD_SSH:-}"
PROD_COMPOSE_DIR="${PROD_COMPOSE_DIR:-/opt/ai-trainer}"
PROD_DB_USER="${PROD_DB_USER:-aitrainer}"
PROD_DB="${PROD_DB:-aitrainer}"
LOCAL_PASSWORD="${LOCAL_PASSWORD:-localdev}"

LOCAL_DB_USER="${POSTGRES_USER:-aitrainer}"
LOCAL_DB="${POSTGRES_DB:-aitrainer}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "${REPO_ROOT}/compose.local.yml")
DUMP="${REPO_ROOT}/.prod-dump.sql"

if [[ -z "${PROD_SSH}" ]]; then
  echo "error: set PROD_SSH, e.g. PROD_SSH=root@your-host $0" >&2
  exit 1
fi

cleanup() {
  if [[ "${KEEP_DUMP:-0}" != "1" && -f "${DUMP}" ]]; then
    rm -f "${DUMP}"
  fi
}
trap cleanup EXIT

echo "==> Dumping ${PROD_DB} from ${PROD_SSH}"
ssh "${PROD_SSH}" \
  "cd '${PROD_COMPOSE_DIR}' && docker compose exec -T postgres \
     pg_dump -U '${PROD_DB_USER}' -d '${PROD_DB}' \
     --clean --if-exists --no-owner --no-privileges" \
  > "${DUMP}"
echo "    $(wc -c < "${DUMP}") bytes"

echo "==> Starting local postgres"
"${COMPOSE[@]}" up -d postgres
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T postgres pg_isready -U "${LOCAL_DB_USER}" -d "${LOCAL_DB}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> Restoring into local postgres"
"${COMPOSE[@]}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${LOCAL_DB_USER}" -d "${LOCAL_DB}" -q < "${DUMP}"

echo "==> Sanitizing"
"${COMPOSE[@]}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${LOCAL_DB_USER}" -d "${LOCAL_DB}" <<'SQL'
BEGIN;
DELETE FROM strava_tokens;
DELETE FROM intervals_tokens;
DELETE FROM strava_import_jobs;
UPDATE users SET
  strava_auto_sync_enabled = false,
  intervals_auto_sync_enabled = false,
  user_openai_api_key = NULL,
  user_gemini_api_key = NULL;
COMMIT;
SQL

echo "==> Setting local login password"
HASH="$("${COMPOSE[@]}" run --rm -T --no-deps \
  -e NEW_PASSWORD="${LOCAL_PASSWORD}" \
  backend python -c 'import os, auth; print(auth.hash_password(os.environ["NEW_PASSWORD"]))' \
  2>/dev/null | tr -d '\r' | tail -n1)"

if [[ -z "${HASH}" ]]; then
  echo "error: could not generate password hash" >&2
  exit 1
fi

"${COMPOSE[@]}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${LOCAL_DB_USER}" -d "${LOCAL_DB}" \
  -v pwhash="${HASH}" \
  -c "UPDATE users SET hashed_password = :'pwhash';"

echo
echo "Done. Bring the rest up with:"
echo "  docker compose -f compose.local.yml up -d --build"
echo
echo "  frontend  http://localhost:5173"
echo "  backend   http://localhost:8000"
echo "  login     <your prod email> / ${LOCAL_PASSWORD}"
