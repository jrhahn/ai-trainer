#!/usr/bin/env bash
#
# Forced command for the key that collects backups from this host.
#
# The backup copy lives on a machine that is deliberately not this one, and the
# transfer is a *pull*: that host reaches in and takes the artefacts, rather than
# this host pushing them out. The reason is what each end then holds. A pushing
# host needs credentials for the backup store, which means a compromise of the
# machine holding the athletes' data also reaches the backups of it — the one
# failure a backup exists to survive. Pulling moves those credentials off this
# host entirely (ai-trainer-ops#13).
#
# What is left to limit is the other direction: the key the fetching host uses
# must not be a root shell here. Install it with this as its forced command:
#
#   # /root/.ssh/authorized_keys on this host
#   command="/opt/ai-trainer/scripts/backup-over-ssh.sh",restrict ssh-ed25519 AAAA... backup-fetch
#
# `restrict` turns off port forwarding, agent forwarding, X11 and PTY
# allocation; this script turns off everything except reading the backup
# directory. The key then cannot take a backup, delete one, or read anything
# else — it can only collect what is already there.
#
# Checking the shape of the request rather than pinning the exact command string:
# the usual recipe hard-codes `rsync --server --sender -logDtpre.iLsfxC . <dir>`,
# whose middle argument encodes rsync's protocol options and changes with rsync
# versions on either side. Pinned, it breaks on an upgrade; and a restriction
# that breaks gets removed.

set -euo pipefail

# No globbing, because the command below is expanded by word splitting. Shell
# operators in SSH_ORIGINAL_COMMAND are harmless -- word splitting does not
# re-parse `;` or `&&`, so they arrive as literal rsync arguments -- but a `*`
# would still expand against the filesystem.
set -f

ALLOWED_DIR="${BACKUP_SSH_ALLOWED_DIR:-/var/backups/ai-trainer}"

refuse() {
  printf 'backup-over-ssh: %s\n' "$1" >&2
  printf 'This key may only run: rsync --server --sender ... %s/\n' "${ALLOWED_DIR}" >&2
  exit 1
}

command_string="${SSH_ORIGINAL_COMMAND:-}"
[ -n "${command_string}" ] || refuse "this key takes no interactive session"

read -r -a argv <<<"${command_string}"

[ "${argv[0]:-}" = "rsync" ] || refuse "not an rsync invocation"
[ "${argv[1]:-}" = "--server" ] || refuse "rsync without --server"
# --sender is rsync's word for "you are reading, not writing". Without this a
# client could upload *into* the backup directory, which is a way to destroy
# backups rather than read them.
[ "${argv[2]:-}" = "--sender" ] || refuse "rsync without --sender: this key cannot write"

# And the path, because --sender alone would happily serve any file on the host.
# The last argument is the source rsync was asked for.
requested="${argv[${#argv[@]}-1]}"
case "${requested%/}" in
  "${ALLOWED_DIR%/}") ;;
  *) refuse "asked for ${requested}, which is not the backup directory" ;;
esac

exec ${command_string}
