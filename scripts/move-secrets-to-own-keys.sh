#!/usr/bin/env bash
#
# Move every stored secret onto its purpose's own key, on a running deployment
# (ai-trainer-ops#299). The deploy runs this right after it starts the stack.
#
# Since #12 each secret type can have a key of its own in front of the shared
# SECRETS_ENCRYPTION_KEY, but a row moves onto it only when it is next written,
# and a TOTP secret practically never is. backend/scripts/reencrypt_secrets.py
# moves them all; this runs it the way its docstring asks:
#
#   1. a dry run. Nothing to move (no dedicated key set, or everything moved on
#      an earlier deploy) and it stops here, which is every deploy but one;
#   2. a fresh backup (scripts/backup.sh), so the rows as they were are on disk
#      with the keys that open them;
#   3. the move (--commit): one transaction, and every rewritten value is opened
#      with its own key alone before anything is committed;
#   4. scripts.verify_restore, which opens every encrypted column.
#
# Any failure exits non-zero and so fails the deploy. Up to step 3 nothing has
# been written.
#
# Usage (as the deploy calls it):
#   APP_DIR=/opt/ai-trainer BACKUP_PASSPHRASE_FILE=/root/ai-trainer-backup.pass \
#     ./scripts/move-secrets-to-own-keys.sh
#
#   COMPOSE_CMD   default "docker compose --ansi never"
#   BACKUP_CMD    default this directory's backup.sh
#   WAIT_SECONDS  how long to wait for a healthy backend, default 300

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-trainer}"
COMPOSE_CMD="${COMPOSE_CMD:-docker compose --ansi never}"
BACKUP_CMD="${BACKUP_CMD:-$(cd "$(dirname "$0")" && pwd)/backup.sh}"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
export APP_DIR COMPOSE_CMD

compose() {
  ${COMPOSE_CMD} --project-directory "${APP_DIR}" "$@"
}

say() { printf 'move-secrets: %s\n' "$*"; }
die() {
  printf 'move-secrets: %s\n' "$*" >&2
  exit 1
}

# The dry run reads the database through the backend, which the deploy has only
# just started.
waited=0
until compose ps --format json backend 2>/dev/null | grep -Eq '"Health": ?"healthy"'; do
  [ "${waited}" -lt "${WAIT_SECONDS}" ] \
    || die "the backend was not healthy after ${WAIT_SECONDS} s; nothing was written"
  sleep 5
  waited=$((waited + 5))
done

dry="$(compose exec -T backend python -m scripts.reencrypt_secrets)" || {
  printf '%s\n' "${dry}"
  die "the dry run failed; nothing was written"
}
printf '%s\n' "${dry}"
to_move="$(printf '%s\n' "${dry}" | sed -n 's/^\([0-9][0-9]*\) would move\..*/\1/p')"
[ -n "${to_move}" ] || die "could not read from the dry run how many would move; nothing was written"
if [ "${to_move}" -eq 0 ]; then
  say "nothing to move"
  exit 0
fi

if [ -z "${BACKUP_PASSPHRASE_FILE:-}" ] || [ ! -r "${BACKUP_PASSPHRASE_FILE}" ]; then
  die "${to_move} would move, but there is no backup passphrase for the backup first; nothing was written"
fi
say "a fresh backup before ${to_move} move"
BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE}" ${BACKUP_CMD} \
  || die "the backup failed; nothing was written"

compose exec -T backend python -m scripts.reencrypt_secrets --commit \
  || die "the move failed; its transaction wrote nothing"
compose exec -T backend python -m scripts.verify_restore \
  || die "verify_restore failed after the move: restore from the backup just taken"
say "moved ${to_move}; every encrypted column opens"
