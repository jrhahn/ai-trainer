"""Name the keys a dump was written under, so a restore can check before it runs.

The dangerous restore is not the one that fails. It is the one that succeeds:
Postgres accepts the dump, the app boots, the dashboard looks right, and every
Strava token, intervals.icu key and TOTP secret in it is ciphertext nobody holds
the key to any more. Nothing reports that, because at the database layer nothing
is wrong — the rows are exactly what was backed up. The athlete finds out when
their second factor stops working (ai-trainer-ops#13).

So the dump travels with a fingerprint of the key that encrypts each purpose, and
``scripts/restore.sh`` compares it against the keys the target actually has
*before* loading anything. A mismatch becomes a refusal naming the purpose,
instead of silence followed by a support request six weeks later.

Why a fingerprint and not the keys themselves
---------------------------------------------
The manifest sits next to the dump, and the whole point of encrypting these
columns is that the ciphertext and the key are not in the same place. A manifest
carrying the keys would hand both to whoever reads the backup directory, which is
worse than not encrypting the columns at all — it looks protected and is not.

A SHA-256 of a 32-byte random Fernet key reveals nothing: there is no dictionary
to run against it. It is domain-separated with a prefix so the digest cannot be
compared against some other system's hash of the same key.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from config import _DEDICATED_KEY_FIELDS, SecretPurpose, settings

_FINGERPRINT_DOMAIN = b"ai-trainer-backup-key-fingerprint:v1:"

PLAINTEXT = "plaintext"
"""No key at all — the columns of that purpose are stored unencrypted.

A development deployment, and a legitimate state to back up. It is named rather
than omitted so a restore can tell "this purpose had no key" apart from "this
manifest predates that purpose", which are opposite situations: the first is
fine, the second means the manifest is too old to be trusted about anything.
"""


def fingerprint(key: str) -> str:
    """A name for *key* that can be written down next to the data it opens."""
    digest = hashlib.sha256(_FINGERPRINT_DOMAIN + key.encode()).hexdigest()
    return digest[:16]


def key_fingerprints() -> dict[str, str]:
    """The fingerprint of whichever key currently *encrypts* each purpose.

    The first key of the chain, not all of them, because that is the one a
    restored row will have been written under. ``MultiFernet`` decrypts with any
    key in the chain (#12), so a target holding a superset still works — the
    check below is deliberately "can you open this", not "is your configuration
    identical to mine".

    Derived from :data:`SecretPurpose.ALL` rather than listed here, so a fifth
    secret type cannot be added to the model and silently left out of the
    backup. That failure has happened once already in this repo, in the shape of
    a hand-maintained tuple nobody extended (jrhahn/ai-trainer#754).
    """
    fingerprints: dict[str, str] = {}
    for purpose in SecretPurpose.ALL:
        keys = settings.encryption_keys_for(purpose)
        fingerprints[purpose] = fingerprint(keys[0]) if keys else PLAINTEXT
    return fingerprints


def _shared_key_var() -> str:
    """Which variable the shared key is actually being read from.

    Two can supply it: ``STRAVA_ENCRYPTION_KEY`` is the pre-#613 name and is
    still honoured, so a deployment upgraded rather than freshly installed may
    be running on it. Naming the one in use rather than the one in the docs is
    the difference between a restore check that helps and one that sends the
    operator to an empty variable.
    """
    return (
        "SECRETS_ENCRYPTION_KEY"
        if settings.secrets_encryption_key
        else "STRAVA_ENCRYPTION_KEY"
    )


def key_vars() -> dict[str, str]:
    """Which environment variable fed each purpose's fingerprint.

    Recorded in the manifest so ``scripts/restore.sh`` can check the keys on the
    target without reimplementing this mapping. The restore runs before the
    backend container exists — there is no app to ask — and a copy of the
    purpose-to-variable table in a shell script is exactly the second inventory
    that goes stale. Writing it down at backup time, from the code, means the
    manifest explains itself to whatever reads it later.
    """
    mapping: dict[str, str] = {}
    for purpose in SecretPurpose.ALL:
        if settings._dedicated_key(purpose):
            mapping[purpose] = _DEDICATED_KEY_FIELDS[purpose].upper()
        elif settings.encryption_key:
            mapping[purpose] = _shared_key_var()
        else:
            mapping[purpose] = PLAINTEXT
    return mapping


def manifest_fragment() -> dict[str, Any]:
    """The part of the backup manifest only the application can answer."""
    return {
        "key_fingerprints": key_fingerprints(),
        "key_vars": key_vars(),
        "purposes": list(SecretPurpose.ALL),
        # How to reproduce the fingerprints above, so the restore script does
        # not carry a second copy of the recipe. sha256(domain + key)[:16].
        # Recorded rather than assumed because a manifest outlives the version
        # of the code that wrote it: one from before a change to this scheme
        # stays checkable, instead of failing every purpose at once and looking
        # like the keys are wrong.
        "fingerprint_domain": _FINGERPRINT_DOMAIN.decode(),
        "fingerprint_length": 16,
    }


def main() -> int:
    json.dump(manifest_fragment(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
