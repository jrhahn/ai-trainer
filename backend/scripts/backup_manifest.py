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


def _variable_holding(key: str, purpose: str) -> str:
    """Which environment variable supplies *key* for *purpose*.

    Paired up by value rather than by rebuilding the order of
    ``encryption_keys_for``, so that method stays the only place deciding which
    keys a purpose has and in what order. The two cannot disagree if only one of
    them knows.

    Unambiguous because a dedicated key equal to the shared one is refused at
    boot — it would validate, encrypt and decrypt while giving no independence at
    all, so there is a validator for it (#12).
    """
    if key and key == settings._dedicated_key(purpose):
        return _DEDICATED_KEY_FIELDS[purpose].upper()
    return _shared_key_var()


def key_chains() -> dict[str, list[dict[str, str]]]:
    """Every key that may decrypt each purpose, encrypting key first.

    **The whole chain, not just the encrypting key.** Recording only the first
    one was a real hole, and the common case rather than an edge: ``MultiFernet``
    encrypts with the first key and decrypts with any, so rows written before a
    purpose gained a dedicated key are still under the shared one — which, right
    after the #12 split, is *every existing row*. A manifest naming only the
    dedicated key lets a restore onto a host with the right dedicated key and a
    wrong ``SECRETS_ENCRYPTION_KEY`` print "matches" and proceed, and the legacy
    rows are then exactly the unopenable ciphertext this mechanism exists to
    prevent. `verify_restore` would still catch it, but only after the load.

    So the restore check requires *every* key in the chain. That is deliberately
    strict: the manifest cannot know which keys actually have rows under them
    without scanning the data, and the asymmetry is not close — a refusal costs
    an operator one look at a variable, a false pass costs somebody else's
    credentials permanently.

    An empty list means the purpose was stored as plaintext. Derived from
    :data:`SecretPurpose.ALL`, so a fifth secret type cannot be added to the
    model and silently left out of the backup (jrhahn/ai-trainer#754).
    """
    chains: dict[str, list[dict[str, str]]] = {}
    for purpose in SecretPurpose.ALL:
        chains[purpose] = [
            {"variable": _variable_holding(key, purpose), "fingerprint": fingerprint(key)}
            for key in settings.encryption_keys_for(purpose)
        ]
    return chains


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


def manifest_fragment() -> dict[str, Any]:
    """The part of the backup manifest only the application can answer.

    ``key_chains`` carries the variable names as well as the fingerprints so
    ``scripts/restore.sh`` can check the target's keys without reimplementing the
    purpose-to-variable mapping. The restore runs before the backend container
    exists — there is no app to ask — and a copy of that table in a shell script
    is exactly the second inventory that goes stale.
    """
    return {
        "key_chains": key_chains(),
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
