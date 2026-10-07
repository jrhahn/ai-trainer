#!/usr/bin/env bash
#
# Rehearse the restore. Not "check that backups exist" — actually lose the
# database and get it back, with the secrets in it still readable
# (ai-trainer-ops#13).
#
# This is a committed script and not a story in the runbook because a rehearsal
# nobody can repeat is worth about as much as an untested backup: it tells you
# the procedure worked once, against a version of the code that has since moved.
# Run it before any change to scripts/backup.sh or scripts/restore.sh, and after
# any new encrypted column, and put the date in docs/runbook-restore.md.
#
# What it does
# ------------
#   1. starts a throwaway Postgres, nothing to do with any deployment
#   2. migrates it and writes one real secret of every purpose through the ORM,
#      so the ciphertext is produced by the application and not by this script
#   3. takes a backup with scripts/backup.sh
#   4. destroys the database completely — container and volume
#   5. refuses to restore with the WRONG keys, and fails the drill if it does not
#   6. restores with the right ones via scripts/restore.sh
#   7. checks every secret comes back byte-for-byte equal to what went in
#
# Step 5 is not decoration. Without it, step 7 could pass for the wrong reason —
# a check that never rejects anything would let a restore with last month's
# secrets bundle report success, which is the exact failure this whole mechanism
# exists to prevent. A drill that only tests the happy path cannot tell a working
# guard from an absent one.
#
# What it does NOT cover, and the runbook says so too:
#   - the Compose stack as a whole: traefik, authelia, the frontend, TLS
#   - that the UI works after a restore. Log in by hand; this cannot.
#   - restoring onto a *fresh host*. This reuses the host it runs on, so it
#     proves nothing about a machine that has never had the app installed.
#
# Usage:  ./scripts/rehearse-restore.sh

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${REPO}/backend"

CONTAINER="ai-trainer-restore-drill"
PGPORT="${PGPORT:-55433}"
PGIMAGE="${PGIMAGE:-docker.io/pgvector/pgvector:pg17}"
PGPASSWORD_DRILL="drill-not-a-real-password"

step() { printf '\n\033[1m== %s\033[0m\n' "$1" >&2; }
note() { printf '   %s\n' "$1" >&2; }
fail() { printf '\n\033[1;31mDRILL FAILED: %s\033[0m\n' "$1" >&2; exit 1; }
pass() { printf '   \033[32mok\033[0m %s\n' "$1" >&2; }

if command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
elif command -v podman >/dev/null 2>&1; then
  RUNTIME=podman
else
  fail "neither docker nor podman is installed"
fi

# Checked here rather than where they are first used, so a missing tool is not
# discovered after the drill has already migrated a database and seeded it —
# which is how this script was first run, and the five minutes before the
# failure were five minutes of nothing.
command -v gpg >/dev/null 2>&1 || fail "gpg is not on PATH; backup.sh encrypts the secrets bundle with it"
command -v jq >/dev/null 2>&1 || fail "jq is not on PATH; restore.sh reads the manifest with it"

workdir="$(mktemp -d)"
APP_DIR="${workdir}/app"
BACKUP_DIR="${workdir}/backups"
mkdir -p "${APP_DIR}" "${BACKUP_DIR}"

cleanup() {
  ${RUNTIME} rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  rm -rf "${workdir}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# The shim that lets the real scripts drive a throwaway container
# ---------------------------------------------------------------------------
# backup.sh and restore.sh talk to the deployment through one `compose`
# function, overridable via COMPOSE_CMD. This stands in for it: `exec -T
# postgres` goes to the drill container, `exec -T backend` runs the backend from
# the checkout, and `up -d` is a no-op because the container is already up.
#
# The point of the indirection is that the drill exercises the scripts an
# operator would actually run, down to the checksum comparisons and the
# fingerprint heredoc. Reimplementing their steps here would be a drill of this
# file instead.
cat > "${workdir}/compose-shim" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
args=()
project_dir=""
while [ \$# -gt 0 ]; do
  case "\$1" in
    # Honoured rather than discarded. An earlier version baked the good app
    # directory into this shim, which quietly weakened step 5: restore.sh was
    # pointed at a .env with a wrong key, but verify_restore still ran against
    # the right one, so the second line of defence was never exercised. A shim
    # that ignores what compose is told is a shim that tests something else.
    --project-directory) project_dir="\$2"; shift 2 ;;
    --ansi) shift 2 ;;
    *) args+=("\$1"); shift ;;
  esac
done
set -- "\${args[@]}"
: "\${project_dir:?shim: no --project-directory was passed}"
case "\${1:-}" in
  up) exit 0 ;;
  exec)
    shift
    [ "\${1:-}" = "-T" ] && shift
    service="\$1"; shift
    case "\${service}" in
      postgres)
        exec ${RUNTIME} exec -i -e PGPASSWORD='${PGPASSWORD_DRILL}' "${CONTAINER}" "\$@"
        ;;
      backend)
        cd "${BACKEND}"
        # The backend "container" is the checkout. Drops the `python` the scripts
        # pass and runs the module through uv instead. The environment comes from
        # the .env of whichever project directory compose was pointed at, which
        # is how the container would get it.
        [ "\${1:-}" = "python" ] && shift
        exec env \\
          DATABASE_URL="postgresql+asyncpg://aitrainer:${PGPASSWORD_DRILL}@127.0.0.1:${PGPORT}/aitrainer" \\
          \$(grep -E '^[A-Z_]+=' "\${project_dir}/.env" | xargs) \\
          uv run python "\$@"
        ;;
      *) echo "shim: unexpected service \${service}" >&2; exit 64 ;;
    esac
    ;;
  *) echo "shim: unexpected compose verb \${1:-}" >&2; exit 64 ;;
esac
SHIM
chmod +x "${workdir}/compose-shim"
export COMPOSE_CMD="${workdir}/compose-shim"

export _PYTEST_NIXOS_REEXEC=1
if ls /nix/store/*/lib/libstdc++.so.6 >/dev/null 2>&1; then
  LD_LIBRARY_PATH="$(dirname "$(ls /nix/store/*/lib/libstdc++.so.6 | sort | head -1)")"
  export LD_LIBRARY_PATH
fi

newkey() {
  (cd "${BACKEND}" && uv run python -c \
    'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
}

psql_drill() {
  ${RUNTIME} exec -i -e PGPASSWORD="${PGPASSWORD_DRILL}" "${CONTAINER}" \
    psql -U aitrainer -d aitrainer "$@"
}

public_tables() {
  psql_drill -tAc \
    "select count(*) from information_schema.tables where table_schema='public'" \
    | tr -d '[:space:]'
}

start_drill_postgres() {
  ${RUNTIME} rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  # `rm -f` returns before the container is really gone, and the port stays
  # bound for a moment after it.
  for _ in $(seq 1 30); do
    ${RUNTIME} container exists "${CONTAINER}" 2>/dev/null || break
    sleep 1
  done

  ${RUNTIME} run -d --name "${CONTAINER}" \
    -e POSTGRES_DB=aitrainer \
    -e POSTGRES_USER=aitrainer \
    -e POSTGRES_PASSWORD="${PGPASSWORD_DRILL}" \
    -p "127.0.0.1:${PGPORT}:5432" \
    "${PGIMAGE}" >/dev/null

  # Three consecutive real queries, not one pg_isready. The official entrypoint
  # runs a *temporary* server to initialise the cluster and then shuts it down
  # before starting the real one, and pg_isready says yes to that one too — so a
  # single probe raced the drill into "the database system is shutting down"
  # right after it had reported the database ready.
  local streak=0
  for _ in $(seq 1 90); do
    if psql_drill -tAc 'select 1' >/dev/null 2>&1; then
      streak=$((streak + 1))
      [ "${streak}" -ge 3 ] && return 0
    else
      streak=0
    fi
    sleep 1
  done
  fail "the drill database never came up"
}

# ---------------------------------------------------------------------------
step "1. a throwaway Postgres"
# ---------------------------------------------------------------------------
start_drill_postgres
note "container ${CONTAINER} on 127.0.0.1:${PGPORT}"
pass "postgres is accepting connections"

# ---------------------------------------------------------------------------
step "2. a deployment with a key per purpose, and one secret of each"
# ---------------------------------------------------------------------------
cat > "${APP_DIR}/.env" <<ENV
APP_ENV=production
POSTGRES_DB=aitrainer
POSTGRES_USER=aitrainer
POSTGRES_PASSWORD=${PGPASSWORD_DRILL}
SECRETS_ENCRYPTION_KEY=$(newkey)
STRAVA_TOKEN_ENCRYPTION_KEY=$(newkey)
INTERVALS_ENCRYPTION_KEY=$(newkey)
AI_KEY_ENCRYPTION_KEY=$(newkey)
TOTP_ENCRYPTION_KEY=$(newkey)
JWT_SECRET=drill-jwt-secret-that-is-at-least-32-bytes
STRAVA_CLIENT_ID=drill
STRAVA_CLIENT_SECRET=drill
OPENAI_API_KEY=drill
GEMINI_API_KEY=drill
AUTHELIA_AUTH_ENABLED=false
ENV
chmod 0600 "${APP_DIR}/.env"
mkdir -p "${APP_DIR}/authelia"
printf 'users: {}\n' > "${APP_DIR}/authelia/users_database.yml"

drill_env=(
  "DATABASE_URL=postgresql+asyncpg://aitrainer:${PGPASSWORD_DRILL}@127.0.0.1:${PGPORT}/aitrainer"
)
while IFS= read -r line; do
  case "${line}" in ''|\#*) continue ;; esac
  drill_env+=("${line}")
done < "${APP_DIR}/.env"

note "migrating"
(cd "${BACKEND}" && env "${drill_env[@]}" uv run alembic upgrade head >/dev/null) \
  || fail "alembic upgrade head failed against the drill database"
pass "schema is current"

note "writing one secret of every purpose, through the ORM"
(cd "${BACKEND}" && env "${drill_env[@]}" uv run python - <<'PYTHON') || fail "could not seed the drill database"
import asyncio

import crud
import models
from database import async_session_maker

SECRETS = {
    "totp_secret": "TOTPSECRET234567",
    "user_openai_api_key": "sk-drill-openai",
    "strava_access": "drill-strava-access",
    "strava_refresh": "drill-strava-refresh",
    "intervals_api_key": "drill-intervals-key",
}


async def seed() -> None:
    async with async_session_maker() as session:
        user = await crud.create_user(
            session, email="drill@example.com", name="Drill", hashed_password="x"
        )
        user.totp_secret = SECRETS["totp_secret"]
        user.user_openai_api_key = SECRETS["user_openai_api_key"]
        session.add(
            models.StravaToken(
                user_id=user.id,
                access_token=SECRETS["strava_access"],
                refresh_token=SECRETS["strava_refresh"],
                expires_at=1,
                athlete_id=1,
            )
        )
        session.add(
            models.IntervalsToken(user_id=user.id, api_key=SECRETS["intervals_api_key"])
        )
        await session.commit()


asyncio.run(seed())
PYTHON
pass "secrets of all four purposes stored"

# The ciphertext really is ciphertext — otherwise the whole drill could pass with
# encryption switched off, which is the vacuous-green version of this test.
stored="$(psql_drill -tAc 'select totp_secret from users limit 1')"
case "${stored}" in
  gAAAAA*) pass "stored values are Fernet tokens, not plaintext" ;;
  *) fail "the drill wrote plaintext (${stored:0:12}...) — encryption was not on, so this drill would prove nothing" ;;
esac

# ---------------------------------------------------------------------------
step "3. back it up with scripts/backup.sh"
# ---------------------------------------------------------------------------
printf 'drill-passphrase-not-a-real-one\n' > "${workdir}/passphrase"
chmod 0600 "${workdir}/passphrase"

APP_DIR="${APP_DIR}" BACKUP_DIR="${BACKUP_DIR}" \
  BACKUP_PASSPHRASE_FILE="${workdir}/passphrase" \
  "${REPO}/scripts/backup.sh" || fail "backup.sh failed"

manifest="$(ls -1 "${BACKUP_DIR}"/*.manifest.json | tail -1)"
[ -r "${manifest}" ] || fail "backup.sh produced no manifest"
pass "three artefacts in ${BACKUP_DIR}"
note "$(basename "${manifest}")"

# ---------------------------------------------------------------------------
step "4. destroy the database"
# ---------------------------------------------------------------------------
start_drill_postgres
rows="$(public_tables)"
[ "${rows}" = "0" ] || fail "the database was not actually empty (${rows} tables) — the drill would restore onto existing data and prove nothing"
pass "database is gone: no tables, no volume"

# ---------------------------------------------------------------------------
step "5. the WRONG keys must be refused"
# ---------------------------------------------------------------------------
# A guard that never rejects anything passes step 7 just as happily as a working
# one. This is what tells those two apart.
wrong_app="${workdir}/wrong"
mkdir -p "${wrong_app}"

# Two cases, because they fail for different reasons and only one of them is
# obvious. A wrong *dedicated* key is the case anyone would think to test. A
# wrong *shared* key is the one that shipped broken: the manifest used to record
# only the encrypting key, so correct dedicated keys plus a rotated
# SECRETS_ENCRYPTION_KEY printed "matches" four times and loaded the dump —
# losing every row written before the #12 split, which is most of them.
try_refusal() {
  local label="$1" expect="$2"
  if APP_DIR="${wrong_app}" RESTORE_SKIP_SECRETS=1 \
     "${REPO}/scripts/restore.sh" "${manifest}" >/dev/null 2>"${workdir}/refusal"; then
    fail "restore.sh accepted a .env with ${label}. The fingerprint check is not
working, and every other result in this drill is meaningless."
  fi
  grep -q "${expect}" "${workdir}/refusal" \
    || fail "restore.sh refused ${label}, but without naming ${expect}:
$(cat "${workdir}/refusal")"
  pass "refused ${label}, naming ${expect}"
  note "$(grep -m1 'different key' "${workdir}/refusal" || true)"
}

sed -e "s|^TOTP_ENCRYPTION_KEY=.*|TOTP_ENCRYPTION_KEY=$(newkey)|" \
    "${APP_DIR}/.env" > "${wrong_app}/.env"
chmod 0600 "${wrong_app}/.env"
try_refusal "a wrong dedicated key" "totp"

sed -e "s|^SECRETS_ENCRYPTION_KEY=.*|SECRETS_ENCRYPTION_KEY=$(newkey)|" \
    "${APP_DIR}/.env" > "${wrong_app}/.env"
chmod 0600 "${wrong_app}/.env"
try_refusal "a wrong shared key and correct dedicated ones" "SECRETS_ENCRYPTION_KEY"

# Nothing was loaded by that attempt.
rows="$(public_tables)"
[ "${rows}" = "0" ] || fail "the refused restore loaded ${rows} tables anyway — it must refuse *before* touching the database"
pass "and it refused before loading anything"

# ---------------------------------------------------------------------------
step "6. restore with the right keys"
# ---------------------------------------------------------------------------
APP_DIR="${APP_DIR}" RESTORE_SKIP_SECRETS=1 \
  "${REPO}/scripts/restore.sh" "${manifest}" || fail "restore.sh failed with the correct keys"
pass "restore.sh completed, including verify_restore"

# ---------------------------------------------------------------------------
step "7. every secret came back, byte for byte"
# ---------------------------------------------------------------------------
# verify_restore already said the values decrypt. This asks the stricter
# question: do they decrypt to what was stored? A key that decrypts to the wrong
# plaintext is not a thing Fernet permits, but a restore that silently loaded a
# *different* backup is, and that is what this catches.
(cd "${BACKEND}" && env "${drill_env[@]}" uv run python - <<'PYTHON') || fail "the restored secrets are not the ones that went in"
import asyncio
import sys

from sqlalchemy import select

import models
from database import async_session_maker

EXPECTED = {
    "totp_secret": "TOTPSECRET234567",
    "user_openai_api_key": "sk-drill-openai",
    "strava_access": "drill-strava-access",
    "strava_refresh": "drill-strava-refresh",
    "intervals_api_key": "drill-intervals-key",
}


async def check() -> int:
    async with async_session_maker() as session:
        user = (
            await session.execute(
                select(models.User).where(models.User.email == "drill@example.com")
            )
        ).scalar_one_or_none()
        if user is None:
            print("   the restored database has no drill user at all", file=sys.stderr)
            return 1

        strava = (
            await session.execute(
                select(models.StravaToken).where(models.StravaToken.user_id == user.id)
            )
        ).scalar_one()
        intervals = (
            await session.execute(
                select(models.IntervalsToken).where(
                    models.IntervalsToken.user_id == user.id
                )
            )
        ).scalar_one()

        actual = {
            "totp_secret": user.totp_secret,
            "user_openai_api_key": user.user_openai_api_key,
            "strava_access": strava.access_token,
            "strava_refresh": strava.refresh_token,
            "intervals_api_key": intervals.api_key,
        }

    bad = {k: v for k, v in actual.items() if v != EXPECTED[k]}
    for name, value in bad.items():
        shown = (value or "")[:16]
        print(f"   {name}: expected {EXPECTED[name]!r}, got {shown!r}...", file=sys.stderr)
    if bad:
        return 1
    for name in EXPECTED:
        print(f"   ok {name}")
    return 0


raise SystemExit(asyncio.run(check()))
PYTHON

printf '\n\033[1;32mDRILL PASSED\033[0m — %s\n' "$(date -u +%Y-%m-%d)" >&2
printf 'Put this date in docs/runbook-restore.md.\n' >&2
printf 'Not covered: the Compose stack, TLS, the UI, and restoring onto a fresh host.\n' >&2
