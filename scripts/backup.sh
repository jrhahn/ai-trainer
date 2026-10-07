#!/usr/bin/env bash
#
# Back up a running deployment: the database, and the secrets that are not in it.
#
# The second half is the point (ai-trainer-ops#13). A dump of this database
# without the Fernet keys is not a degraded backup, it is a decoy: it restores
# cleanly, the app boots, and every Strava token, intervals.icu key and TOTP
# secret in it is ciphertext nobody can open. Likewise the Authelia user file —
# with AUTHELIA_AUTH_ENABLED the passwords live there and `users.hashed_password`
# holds a throwaway, so a restored database without it is a list of accounts
# nobody can log into.
#
# Three artefacts per run, deliberately not one:
#
#   <stamp>.dump               the database        (pg_dump custom format)
#   <stamp>.secrets.tar.gz.gpg .env + authelia/    (symmetric, AES-256)
#   <stamp>.manifest.json      neither            (safe to read, see below)
#
# Not one bundle, because the reason those columns are encrypted at all is that
# the ciphertext and the key should not sit in the same place. A single tarball
# of dump-plus-keys hands both to whoever can read the backup directory, which is
# strictly worse than not encrypting the columns: it looks protected and is not.
#
# The manifest holds no secrets and is what makes the separation survivable: it
# carries a *fingerprint* of the key encrypting each purpose, so
# `scripts/restore.sh` can tell you before it loads anything whether the keys you
# have will open this dump. See backend/scripts/backup_manifest.py.
#
# Usage:
#   BACKUP_PASSPHRASE_FILE=/root/backup.pass ./scripts/backup.sh
#
# Environment:
#   APP_DIR                  deployment directory       (default /opt/ai-trainer)
#   BACKUP_DIR               where artefacts land       (default /var/backups/ai-trainer)
#   BACKUP_PASSPHRASE_FILE   passphrase for the secrets bundle (required)
#   BACKUP_KEEP              how many runs to keep      (default 14)

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-trainer}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ai-trainer}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

die() {
  printf 'backup: %s\n' "$1" >&2
  exit 1
}

note() { printf 'backup: %s\n' "$1" >&2; }

[ -n "${BACKUP_PASSPHRASE_FILE:-}" ] || die \
  "BACKUP_PASSPHRASE_FILE is not set. The secrets bundle holds every encryption
key this deployment has; writing it unencrypted would put the keys next to the
ciphertext they open. Generate one with: openssl rand -base64 48"

[ -r "${BACKUP_PASSPHRASE_FILE}" ] || die \
  "cannot read ${BACKUP_PASSPHRASE_FILE}"

[ -d "${APP_DIR}" ] || die "no deployment at ${APP_DIR} (set APP_DIR)"
[ -r "${APP_DIR}/.env" ] || die "no .env at ${APP_DIR} — nothing to capture the keys from"

command -v gpg >/dev/null || die "gpg is not installed (apt-get install -y gnupg)"

mkdir -p "${BACKUP_DIR}"
chmod 0700 "${BACKUP_DIR}"

# The artefacts are what leaves the host. A passphrase stored inside the
# directory that gets shipped off-host travels with the thing it protects, which
# is the same as not having one — so this is a refusal, not a warning.
passphrase_real="$(readlink -f "${BACKUP_PASSPHRASE_FILE}")"
backup_real="$(readlink -f "${BACKUP_DIR}")"
case "${passphrase_real}" in
  "${backup_real}"/*) die \
    "the passphrase file is inside ${BACKUP_DIR}. Whatever copies the backups
off this host would copy the passphrase with them; keep it elsewhere." ;;
esac

# Overridable so scripts/rehearse-restore.sh can point this at a throwaway
# container, and so a host running podman instead of docker is not stuck. The
# rehearsal driving *this* script rather than a copy of its steps is the only
# way the drill proves anything about what runs in production.
COMPOSE_CMD="${COMPOSE_CMD:-docker compose --ansi never}"

compose() {
  ${COMPOSE_CMD} --project-directory "${APP_DIR}" "$@"
}

# shellcheck disable=SC1091
set -a; . "${APP_DIR}/.env"; set +a
POSTGRES_USER="${POSTGRES_USER:-aitrainer}"
POSTGRES_DB="${POSTGRES_DB:-aitrainer}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="${BACKUP_DIR}/${stamp}.dump"
secrets="${BACKUP_DIR}/${stamp}.secrets.tar.gz.gpg"
manifest="${BACKUP_DIR}/${stamp}.manifest.json"

staging="$(mktemp -d)"
completed=0

# A failed run leaves nothing behind. Pruning keys off `*.manifest.json`, and the
# manifest is written last, so an aborted run used to leave a `.dump` and a
# `.secrets.tar.gz.gpg` that nothing would ever delete: a nightly job against a
# stopped Postgres would grow the directory without bound, and — worse than the
# disk — accumulate copies of the secrets bundle there, each a full set of this
# deployment's keys.
cleanup() {
  rm -rf "${staging}"
  if [ "${completed}" -eq 0 ]; then
    rm -f "${dump}" "${secrets}" "${manifest}"
  fi
}
trap cleanup EXIT

umask 0077

# ---------------------------------------------------------------------------
# Secrets first, then the database. The order is not arbitrary.
#
# An account registered between the two ends up in the dump without its
# credential in the captured user file, so after a restore it cannot be logged
# into — which an operator notices and fixes by adding the entry. The other
# order loses that race the expensive way: the credential is captured, the row
# is not, and `POST /auth/login` recreates a missing row from a valid credential
# (#14), so the athlete logs in successfully to an account with none of their
# history in it. That reads as data loss rather than as a failed restore.
#
# Same reasoning as the deletion order in backend/routers/users.py: when two
# writes cannot be made atomic, pick the order whose failure is the loud one.
# ---------------------------------------------------------------------------

note "capturing secrets"
secrets_staging="${staging}/secrets"
mkdir -p "${secrets_staging}"
cp "${APP_DIR}/.env" "${secrets_staging}/.env"
if [ -d "${APP_DIR}/authelia" ]; then
  cp -a "${APP_DIR}/authelia" "${secrets_staging}/authelia"
else
  note "no ${APP_DIR}/authelia — header auth is not configured on this host"
fi

tar -C "${secrets_staging}" -czf "${staging}/secrets.tar.gz" .
gpg --batch --yes --quiet \
    --symmetric --cipher-algo AES256 \
    --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
    --output "${secrets}" \
    "${staging}/secrets.tar.gz"
chmod 0600 "${secrets}"

note "dumping the database"
# --format=custom so pg_restore can be pointed at a fresh cluster without
# editing SQL by hand, and because it compresses. pg_dump runs in one snapshot,
# so the dump is internally consistent without stopping the backend.
compose exec -T postgres pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --username "${POSTGRES_USER}" \
  "${POSTGRES_DB}" > "${dump}"
chmod 0600 "${dump}"

[ -s "${dump}" ] || die "pg_dump produced an empty file"

note "writing the manifest"
# The fingerprints come from the application, not from this script: it is the
# code that knows which key encrypts which purpose, and a second list here would
# be a list to forget to extend. jrhahn/ai-trainer#754 is what that costs.
fragment="$(compose exec -T backend python -m scripts.backup_manifest)"

alembic_revision="$(compose exec -T postgres psql \
  --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --tuples-only --no-align \
  --command 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
[ -n "${alembic_revision}" ] || die \
  "could not read alembic_version. Either the schema was never migrated, or
this is not the database the app uses — in both cases the dump is not the
thing you want to be relying on."

dump_sha="$(sha256sum "${dump}" | cut -d' ' -f1)"
secrets_sha="$(sha256sum "${secrets}" | cut -d' ' -f1)"

printf '%s' "${fragment}" \
  | STAMP="${stamp}" \
    ALEMBIC_REVISION="${alembic_revision}" \
    DUMP_SHA="${dump_sha}" \
    SECRETS_SHA="${secrets_sha}" \
    MANIFEST_POSTGRES_DB="${POSTGRES_DB}" \
    MANIFEST_POSTGRES_USER="${POSTGRES_USER}" \
    python3 -c '
import json, os, sys

fragment = json.load(sys.stdin)
fragment.update(
    taken_at=os.environ["STAMP"],
    alembic_revision=os.environ["ALEMBIC_REVISION"],
    dump_sha256=os.environ["DUMP_SHA"],
    secrets_sha256=os.environ["SECRETS_SHA"],
    postgres_db=os.environ["MANIFEST_POSTGRES_DB"],
    postgres_user=os.environ["MANIFEST_POSTGRES_USER"],
    # 2 since the manifest records the whole key chain per purpose and not only
    # the key that encrypts new rows. restore.sh refuses a version 1 manifest
    # rather than running a check that cannot see the shared key.
    manifest_version=2,
)
json.dump(fragment, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write("\n")
' > "${manifest}"
chmod 0600 "${manifest}"

# From here the three artefacts are a complete set, so the cleanup trap must
# stop treating them as the debris of a failed run.
completed=1

# Pruning is last, so a run that failed anywhere above leaves every older
# backup in place. Deleting yesterday's good backup as the final step of
# producing today's broken one is how a backup system becomes the outage.
if [ "${BACKUP_KEEP}" -gt 0 ]; then
  ls -1 "${BACKUP_DIR}"/*.manifest.json 2>/dev/null \
    | sort -r \
    | tail -n "+$((BACKUP_KEEP + 1))" \
    | while read -r old; do
        old_stamp="$(basename "${old}" .manifest.json)"
        note "pruning ${old_stamp}"
        rm -f "${BACKUP_DIR}/${old_stamp}".dump \
              "${BACKUP_DIR}/${old_stamp}".secrets.tar.gz.gpg \
              "${BACKUP_DIR}/${old_stamp}".manifest.json
      done
fi

note "done: ${stamp} (schema ${alembic_revision})"
note "these artefacts are worth nothing until they are off this host"
