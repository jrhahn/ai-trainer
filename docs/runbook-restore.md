# Runbook: backup and restore

> **Last successful rehearsal: 2026-10-07** — `scripts/rehearse-restore.sh`, all
> seven steps, against Postgres 17 (pgvector) with a separate key per secret
> type. Re-run it after any change to the backup scripts or any new encrypted
> column, and update this date.

> **Two manual steps stand between this and a running backup** — a passphrase on
> the VPS and a fetch key on the home server. Until both are done, nothing is
> scheduled and this deployment is not being backed up. See
> [Setting it up](#setting-it-up).

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

The manifest is what makes the separation survivable. For each purpose it
records **every key in its decryption chain** — each as a
`sha256(domain + key)[:16]` fingerprint, which reveals nothing about a 32-byte
random key, plus the name of the variable it came from. `scripts/restore.sh`
compares those against the keys on the target *before loading anything*, so a
mismatch is a refusal naming the purpose and the variable instead of a
clean-looking restore and a support request six weeks later.

**The whole chain and not just the encrypting key**, which is the difference
between a check that works and one that looks like it does. `MultiFernet`
encrypts with the first key and decrypts with any, so rows written before a
purpose gained a dedicated key are still under `SECRETS_ENCRYPTION_KEY` — and
right after the ai-trainer-ops#12 split, that is *every existing row*. Checking
only the dedicated key would print "matches" on a host with a rotated shared key
and load the dump, losing precisely the data most likely to be in it.

Manifests are versioned for this reason: `restore.sh` refuses a version 1
manifest, which recorded only the encrypting key, rather than running a check
that silently drops half its subject.

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

## Where the copies go

Onto the Hetzner Storage Box that already holds the home server's backups,
reusing its Borg setup — `repokey-blake2` encryption, 7 daily / 4 weekly /
12 monthly retention, and the mail that reports a failed job.

```
03:00  ai-trainer-backup.timer        on the VPS      → /var/backups/ai-trainer
03:30  fetch-ai-trainer-backup.timer  on home server  → /srv/backups/ai-trainer
04:00  borgbackup-job-family-local                    → /srv/backups/borg-local
04:30  borgbackup-job-family-hetzner                  → Storage Box
```

**Pulled, not pushed, and that is the point.** A host that pushes its own
backups needs credentials for the backup store, so whoever takes the production
host also reaches the backups of the data it was holding — the one failure a
backup exists to survive. Here the VPS holds no Storage Box credentials at all;
the home server reaches in and collects. The cost is that the home server has to
be up, which is what the staleness check below is for.

Two restrictions make that pull narrow rather than a root shell:

- the fetching key's forced command is `scripts/backup-over-ssh.sh`, which
  permits `rsync --server --sender` against the backup directory and nothing
  else. It cannot take a backup, delete one, write into the directory, or read
  any other path.
- the artefacts arrive still GPG-encrypted, so the home server — which *does*
  hold the Borg credentials — cannot read ai-trainer's five encryption keys.
  Compromise of the backup store does not yield them.

### Staleness is an error, not a shrug

`fetch-ai-trainer-backup` fails if there is no manifest newer than two days,
which sends the failure mail. Without that check a broken VPS timer would be
invisible: the fetch would succeed against a stale directory, Borg would archive
last week's artefacts every night and report success, and the first time anyone
noticed would be a restore.

### Setting it up

One-time, and only the first two need doing by hand:

1. **On the VPS**, create the passphrase for the secrets bundle and keep a copy
   somewhere that is not this host and not the Storage Box — a password manager:

   ```bash
   openssl rand -base64 48 > /root/ai-trainer-backup.pass
   chmod 0600 /root/ai-trainer-backup.pass
   ```

   The next deploy then installs `ai-trainer-backup.timer`. Without this file it
   installs nothing and says so in the run log — a host with no passphrase has
   no timer rather than a unit that fails nightly.

2. **On the home server**, a key for the fetch, and its public half on the VPS
   with the forced command:

   ```bash
   # home server
   ssh-keygen -t ed25519 -N "" -f /var/lib/secrets/ai-trainer-backup-ed25519

   # VPS, in /root/.ssh/authorized_keys, one line:
   command="/opt/ai-trainer/scripts/backup-over-ssh.sh",restrict ssh-ed25519 AAAA... backup-fetch
   ```

3. **In the home server's private `local.nix`:**

   ```nix
   server.backups.aiTrainer = {
     enable = true;
     host = "trainlikea.pro";
   };
   ```

   Then `nixos-rebuild switch`. It asserts `backups.hetzner.enable`, since
   without the Storage Box the fetched artefacts never leave that machine either.

### What is still not covered

- **Restoring onto a genuinely fresh host** has not been rehearsed — the drill
  reuses the machine it runs on. In particular nothing has verified that
  `APP_UID` matches the restored `authelia/` ownership, which has taken down
  login twice (#682, #684).
- **The Compose stack, TLS and the UI.** Log in by hand after a restore.
- **A restore from the Storage Box end to end.** The drill backs up and restores
  locally; nobody has yet pulled a Borg archive back down and restored from it.
  That is the obvious next rehearsal, and it needs the above running first so
  there is an archive to pull.
