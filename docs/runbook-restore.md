# Runbook: backup and restore

> **Last successful rehearsal: 2026-10-07** — `scripts/rehearse-restore.sh`, all
> seven steps, against Postgres 17 (pgvector) with a separate key per secret
> type. Re-run it after any change to the backup scripts or any new encrypted
> column, and update this date.

> **Not yet true in production: nothing is scheduled.** The scripts below work
> and have been rehearsed, but no timer runs them on the live host and nothing
> ships the artefacts off it. Until that is done there is still no backup of
> this deployment — see [What is still missing](#what-is-still-missing).

## The thing that actually goes wrong

Not "the dump failed to load". A restore of this application fails by
**succeeding**:

1. `pg_restore` accepts the dump, migrations are current, the app boots,
   `/healthz` is green, the dashboard renders.
2. Every Strava token, intervals.icu key, AI provider key and TOTP secret in the
   database is Fernet ciphertext that no configured key opens.
3. Nothing says so. `EncryptedString.process_result_value` catches
   `InvalidToken`, logs a warning and **returns the raw ciphertext as the
   value** — deliberately, so rows predating encryption keep working
   (`backend/models.py`).
4. The app sends a ciphertext blob to Strava, gets a 401, and tells the athlete
   to reconnect. Their authenticator simply never matches again.

So: **a database without its encryption keys is not a degraded backup, it is a
decoy.** Everything below is arranged around that one fact.

## What a backup consists of

`scripts/backup.sh` writes three files per run, deliberately not one:

| File | Holds | Safe to read? |
|---|---|---|
| `<stamp>.dump` | the database (`pg_dump --format=custom`) | encrypted at column level |
| `<stamp>.secrets.tar.gz.gpg` | `.env` and `authelia/` | **no** — every key is in here |
| `<stamp>.manifest.json` | fingerprints, checksums, schema revision | yes, holds no keys |

They are three because the reason those columns are encrypted at all is that the
ciphertext and the key should not sit in the same place. A single tarball of
dump-plus-keys hands both to anyone who can read the backup directory, which is
strictly worse than not encrypting the columns: it looks protected and is not.

The manifest is what makes the separation survivable. It records a
**fingerprint** — `sha256(domain + key)[:16]`, which reveals nothing about a
32-byte random key — of whichever key encrypts each purpose, and the name of the
variable it came from. `scripts/restore.sh` compares those against the keys on
the target *before loading anything*, so a mismatch is a refusal naming the
purpose instead of a clean-looking restore and a support request six weeks later.

### What is in the secrets bundle and why each matters

| Lost | Consequence | Recoverable? |
|---|---|---|
| `SECRETS_ENCRYPTION_KEY` | every secret written before the per-purpose split | **no** |
| `STRAVA_TOKEN_ENCRYPTION_KEY` | Strava tokens → every athlete reconnects | no, but self-healing on reconnect |
| `INTERVALS_ENCRYPTION_KEY` | intervals.icu keys → re-entered by hand | no |
| `AI_KEY_ENCRYPTION_KEY` | the athlete's own provider keys | no |
| `TOTP_ENCRYPTION_KEY` | every second factor → **everyone locked out** | no |
| `authelia/users_database.yml` | with `AUTHELIA_AUTH_ENABLED`, *the passwords*. `users.hashed_password` holds a throwaway | no |
| `JWT_SECRET` | everyone is logged out and logs back in | **yes**, harmless |
| `POSTGRES_PASSWORD` | the backend cannot reach its own database | yes, reset it |

The last two are the only forgiving rows in that table. Everything above them is
somebody else's credential.

## Taking a backup

```bash
# once, and never inside BACKUP_DIR — see below
openssl rand -base64 48 > /root/ai-trainer-backup.pass
chmod 0600 /root/ai-trainer-backup.pass

BACKUP_PASSPHRASE_FILE=/root/ai-trainer-backup.pass /opt/ai-trainer/scripts/backup.sh
```

Defaults: `APP_DIR=/opt/ai-trainer`, `BACKUP_DIR=/var/backups/ai-trainer`,
`BACKUP_KEEP=14`.

The script refuses to run if the passphrase file is inside `BACKUP_DIR`.
Whatever copies the backups off the host would copy the passphrase with them,
which is the same as not having one.

**Keep the passphrase somewhere that is not this host and not the backup
target.** A password manager. If it is only on the server, a backup survives
exactly the failures the server survives, which is not the set you are insuring
against.

## Restoring

```bash
BACKUP_PASSPHRASE_FILE=/root/ai-trainer-backup.pass \
  /opt/ai-trainer/scripts/restore.sh /var/backups/ai-trainer/<stamp>.manifest.json
```

In order, it:

1. checks the dump's SHA-256 against the manifest — a truncated `scp` is the
   mundane failure here, and a partial `pg_restore` looks like data loss rather
   than like a copy error;
2. decrypts the secrets bundle and places `.env` and `authelia/`;
3. **compares the key fingerprints and stops if any purpose does not match**;
4. `pg_restore --clean --if-exists --single-transaction`, so a failure leaves an
   empty database rather than a half-populated one;
5. checks the restored schema revision equals the manifest's;
6. brings the stack up and runs `scripts/verify_restore.py`, which opens every
   encrypted column and exits non-zero if anything is unreadable.

It refuses to overwrite an existing `.env` unless `RESTORE_FORCE=1`. Use
`RESTORE_SKIP_SECRETS=1` to keep the host's own `.env` and only check it against
the manifest.

### If step 3 refuses

It names the purpose and the variable. Read it literally: it is telling you that
the `.env` you have did not write this dump. Find the secrets bundle that
belongs to it — do not "fix" the check by restoring anyway, because the result
is the decoy in the first section.

### If step 6 reports unreadable secrets

The database restored and the keys did not match after all, which means the
fingerprint check was bypassed or the bundle was assembled by hand. The
ciphertext is still intact: get the right key and nothing is lost. **Do not let
the app write to those rows in the meantime** — a token refresh will overwrite
recoverable ciphertext with something encrypted under the new key, and then it
really is gone.

### After the script finishes

It says so itself: log in once by hand. The script does not test the UI, TLS, or
whether Traefik got its certificates.

## Rehearsing

```bash
./scripts/rehearse-restore.sh          # needs docker or podman, gpg, jq
```

It starts a throwaway Postgres, writes one real secret of every purpose through
the ORM, backs up with `backup.sh`, destroys the database, **checks that a wrong
key is refused**, restores with `restore.sh`, and verifies every secret comes
back byte-for-byte.

Step 5 — the deliberate failure — is not decoration. A guard that never rejects
anything passes the happy path exactly as happily as a working one, so without
it the drill could not tell a functioning fingerprint check from an absent one.
Removing the refusal from `restore.sh` was tested: the drill fails at step 5.

### What the rehearsal does not cover

Stated plainly, because a drill that is believed to cover more than it does is
worse than none:

- **The Compose stack.** It drives Postgres and the backend from the checkout,
  not traefik, authelia, the frontend or TLS.
- **The UI.** Nothing logs in.
- **A fresh host.** It reuses the machine it runs on, so it proves nothing about
  a server that has never had the app installed — including whether `APP_UID`
  matches the restored `authelia/` ownership, which has broken login twice
  before (#682, #684).
- **The off-host copy**, because there is not one yet.

## What is still missing

As of 2026-10-07 this deployment has **no backup running**. What exists is a
rehearsed procedure, not a running one. Three things remain, and the first two
need a decision rather than code:

1. **Where the artefacts go.** The home server already backs up to a Hetzner
   Storage Box with Borg (`modules/maintenance.nix` in the `home-server` repo),
   but ai-trainer runs on a separate Hetzner VPS at `/opt/ai-trainer` and is in
   none of those jobs. Either that repo's `familyPaths` gains a pulled copy, or
   the VPS gets its own Borg repo on the same Storage Box.
2. **The passphrase's home.** It must not live only on the host being backed up.
3. **A timer**, once 1 and 2 are answered — a systemd timer in
   `deploy/ansible/deploy.yml`, installed only when a passphrase file is present
   so a deploy without one does not start failing.

Until then: run `backup.sh` by hand before anything risky, and copy the three
files somewhere else yourself.
