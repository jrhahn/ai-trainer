#!/usr/bin/env bash
#
# Restore a backup onto an instance, and refuse before loading anything if the
# keys on hand will not open it.
#
# The check in step 3 is the reason this script exists rather than a paragraph
# in the runbook saying "pg_restore the dump". Loading a dump is the easy half
# and it is not where restores go wrong. They go wrong by succeeding with the
# wrong secrets bundle: Postgres accepts everything, the app boots, and the
# stored Strava tokens and TOTP secrets are ciphertext that no configured key
# opens. `EncryptedString` logs a warning and hands back the raw value, so
# nothing fails loudly — see backend/scripts/verify_restore.py
# (ai-trainer-ops#13).
#
# So: compare the key fingerprints in the manifest against the keys the restored
# .env actually contains, name the purpose that does not match, and stop. An
# operator who has reached for last month's secrets bundle finds out in two
# seconds instead of six weeks.
#
# Usage:
#   ./scripts/restore.sh /var/backups/ai-trainer/20261007T120000Z.manifest.json
#
# Environment:
#   APP_DIR                  deployment directory       (default /opt/ai-trainer)
#   BACKUP_PASSPHRASE_FILE   passphrase of the secrets bundle (required)
#   RESTORE_FORCE            set to 1 to overwrite an existing .env
#   RESTORE_SKIP_SECRETS     set to 1 to keep the .env already on the host and
#                            only check it against the manifest

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-trainer}"

die() {
  printf 'restore: %s\n' "$1" >&2
  exit 1
}

note() { printf 'restore: %s\n' "$1" >&2; }

manifest="${1:-}"
[ -n "${manifest}" ] || die "usage: $0 <path to .manifest.json>"
[ -r "${manifest}" ] || die "cannot read ${manifest}"

command -v gpg >/dev/null || die "gpg is not installed (apt-get install -y gnupg)"
command -v jq >/dev/null || die "jq is not installed (apt-get install -y jq)"

stamp="$(basename "${manifest}" .manifest.json)"
backup_dir="$(dirname "${manifest}")"
dump="${backup_dir}/${stamp}.dump"
secrets="${backup_dir}/${stamp}.secrets.tar.gz.gpg"

[ -r "${dump}" ] || die "no dump beside the manifest at ${dump}"

# Overridable so scripts/rehearse-restore.sh can point this at a throwaway
# container, and so a host running podman instead of docker is not stuck. The
# rehearsal driving *this* script rather than a copy of its steps is the only
# way the drill proves anything about what runs in production.
COMPOSE_CMD="${COMPOSE_CMD:-docker compose --ansi never}"

compose() {
  ${COMPOSE_CMD} --project-directory "${APP_DIR}" "$@"
}

staging="$(mktemp -d)"
cleanup() { rm -rf "${staging}"; }
trap cleanup EXIT
umask 0077

# ---------------------------------------------------------------------------
# 1. The artefacts are the ones the manifest describes
# ---------------------------------------------------------------------------
# A truncated transfer is the mundane failure here: these files move over ssh,
# the dump is the big one, and a partial pg_restore leaves a half-populated
# database that looks like data loss rather than like a copy error.
note "checking the artefacts against the manifest"

expect_dump="$(jq -r '.dump_sha256' "${manifest}")"
actual_dump="$(sha256sum "${dump}" | cut -d' ' -f1)"
[ "${expect_dump}" = "${actual_dump}" ] || die \
  "the dump does not match the manifest.
  manifest: ${expect_dump}
  on disk:  ${actual_dump}
Copy it again; do not restore this."

alembic_expected="$(jq -r '.alembic_revision' "${manifest}")"
note "backup taken $(jq -r '.taken_at' "${manifest}"), schema ${alembic_expected}"

# ---------------------------------------------------------------------------
# 2. Put the secrets in place
# ---------------------------------------------------------------------------
if [ "${RESTORE_SKIP_SECRETS:-0}" = "1" ]; then
  note "keeping the .env already on this host (RESTORE_SKIP_SECRETS=1)"
  [ -r "${APP_DIR}/.env" ] || die "RESTORE_SKIP_SECRETS=1 but there is no ${APP_DIR}/.env"
  env_to_check="${APP_DIR}/.env"
else
  [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] || die \
    "BACKUP_PASSPHRASE_FILE is not set and RESTORE_SKIP_SECRETS is not 1"
  [ -r "${secrets}" ] || die "no secrets bundle beside the manifest at ${secrets}"

  expect_secrets="$(jq -r '.secrets_sha256' "${manifest}")"
  actual_secrets="$(sha256sum "${secrets}" | cut -d' ' -f1)"
  [ "${expect_secrets}" = "${actual_secrets}" ] || die \
    "the secrets bundle does not match the manifest. Copy it again."

  note "decrypting the secrets bundle"
  gpg --batch --yes --quiet --decrypt \
      --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
      --output "${staging}/secrets.tar.gz" \
      "${secrets}" \
    || die "could not decrypt the secrets bundle — wrong passphrase file?"

  mkdir -p "${staging}/secrets"
  tar -C "${staging}/secrets" -xzf "${staging}/secrets.tar.gz"
  [ -r "${staging}/secrets/.env" ] || die "the bundle contains no .env"
  env_to_check="${staging}/secrets/.env"
fi

# ---------------------------------------------------------------------------
# 3. The keys on hand open this dump — checked before anything is loaded
# ---------------------------------------------------------------------------
# The recipe comes from the manifest, not from this script: which variable holds
# which purpose's key is a property of the code that took the backup, and a copy
# of that table here would be a second inventory to forget to extend. The
# manifest records it (backend/scripts/backup_manifest.py).
note "checking the keys against the manifest"

MANIFEST="${manifest}" ENV_FILE="${env_to_check}" python3 - <<'PYTHON' || die \
  "the keys do not match this dump. Restoring it would give you a database full
of secrets nobody can decrypt, which is worse than no restore because it looks
like it worked. Find the secrets bundle belonging to this dump."
import hashlib
import json
import os
import sys

manifest = json.load(open(os.environ["MANIFEST"]))
domain = manifest["fingerprint_domain"].encode()
length = manifest["fingerprint_length"]
expected = manifest["key_fingerprints"]
key_vars = manifest["key_vars"]

values = {}
for raw in open(os.environ["ENV_FILE"], encoding="utf-8"):
    raw = raw.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        continue
    name, _, value = raw.partition("=")
    values[name.strip()] = value.strip().strip("'\"")

problems = []
for purpose, want in sorted(expected.items()):
    variable = key_vars.get(purpose, "plaintext")
    if want == "plaintext":
        # The backup was taken with no key for this purpose, so its columns hold
        # plaintext. A target that *does* have a key is fine: nothing stored is
        # encrypted, and new writes will be. Not a problem, worth saying.
        print(f"  {purpose:<9} was stored as plaintext — nothing to open")
        continue
    have = values.get(variable, "")
    if not have:
        problems.append(
            f"  {purpose:<9} needs {variable}, which is empty or absent here"
        )
        continue
    got = hashlib.sha256(domain + have.encode()).hexdigest()[:length]
    if got != want:
        problems.append(
            f"  {purpose:<9} {variable} is a different key than the one that "
            f"encrypted this dump (manifest {want}, this .env {got})"
        )
    else:
        print(f"  {purpose:<9} {variable} matches")

if problems:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)
PYTHON

if [ "${RESTORE_SKIP_SECRETS:-0}" != "1" ]; then
  if [ -e "${APP_DIR}/.env" ] && [ "${RESTORE_FORCE:-0}" != "1" ]; then
    die "${APP_DIR}/.env already exists. This is meant for a fresh instance; set
RESTORE_FORCE=1 if you really mean to overwrite the secrets of a running one."
  fi
  mkdir -p "${APP_DIR}"
  install -m 0600 "${staging}/secrets/.env" "${APP_DIR}/.env"
  if [ -d "${staging}/secrets/authelia" ]; then
    # -a to keep the ownership and mode the bundle recorded. Registration writes
    # users_database.yml through this bind mount and the mount keeps the host's
    # ownership, so getting this wrong takes down login rather than one route
    # (#682, #684).
    cp -a "${staging}/secrets/authelia" "${APP_DIR}/authelia"
    note "placed authelia/ — check 'ls -lan ${APP_DIR}/authelia' against APP_UID in .env"
  fi
  note "placed .env"
fi

# ---------------------------------------------------------------------------
# 4. Load the database
# ---------------------------------------------------------------------------
# shellcheck disable=SC1091
set -a; . "${APP_DIR}/.env"; set +a
POSTGRES_USER="${POSTGRES_USER:-aitrainer}"
POSTGRES_DB="${POSTGRES_DB:-aitrainer}"

note "starting postgres"
compose up -d postgres

for _ in $(seq 1 60); do
  if compose exec -T postgres pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
compose exec -T postgres pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null \
  || die "postgres did not come up"

note "restoring the dump"
# --clean --if-exists so a retry after a failure part-way through does not need
# the volume deleted by hand. --no-owner because the dump was taken that way.
# --single-transaction so a failure leaves an empty database rather than a
# half-populated one that looks restored.
compose exec -T postgres pg_restore \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" \
  --clean --if-exists \
  --no-owner --no-privileges \
  --single-transaction \
  < "${dump}" \
  || die "pg_restore failed; the database is unchanged"

alembic_actual="$(compose exec -T postgres psql \
  --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --tuples-only --no-align \
  --command 'select version_num from alembic_version' | tr -d '[:space:]')"

[ "${alembic_actual}" = "${alembic_expected}" ] || die \
  "restored schema is ${alembic_actual}, manifest says ${alembic_expected}"

# ---------------------------------------------------------------------------
# 5. The product runs, and every secret in it opens
# ---------------------------------------------------------------------------
note "starting the rest of the stack"
compose up -d

note "verifying that the restored secrets decrypt"
compose exec -T backend python -m scripts.verify_restore \
  || die "the database restored but its secrets do not open. See above."

note "restore complete: ${stamp} (schema ${alembic_actual})"
note "log in once before calling it done — this script does not test the UI"
