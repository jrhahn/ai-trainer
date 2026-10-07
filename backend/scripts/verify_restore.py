"""Open every encrypted column and report what could not be opened.

This exists because the failure it looks for is silent by construction. When a
stored secret does not decrypt, ``EncryptedString.process_result_value`` logs a
warning and **returns the ciphertext as if it were the value** (models.py) —
deliberately, so that rows predating encryption keep working. The consequence is
that a database restored without its keys behaves like a healthy one: migrations
apply, the app boots, ``/healthz`` is green, the dashboard renders. The app then
sends a Fernet blob to Strava and gets a 401, and the athlete is told to
reconnect; their TOTP secret simply never matches again. Nothing in that chain
says "restore was incomplete" (ai-trainer-ops#13).

So a restore is not finished when Postgres accepts the dump. It is finished when
this says every purpose is readable.

Run after any restore, and as the last step of a rehearsal:

    docker compose exec -T backend python -m scripts.verify_restore

Exit status is 1 if anything is unreadable, so it can gate a script.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Column, Table, Text, select, type_coerce

from config import SecretPurpose, settings
from database import async_session_maker
from models import Base, EncryptedString

_FERNET_PREFIX = "gAAAAA"
"""What a Fernet token starts with.

Version byte 0x80 and a 64-bit timestamp, base64url-encoded. Used to tell "this
value was never encrypted" apart from "this value is encrypted and I cannot open
it" — the first is a row from before the key was configured and is fine, the
second is the thing this script exists to find. Guessing wrong in the safe
direction would report a plaintext row as a disaster; guessing wrong in the
unsafe direction is not possible, because a value that does not start with this
cannot be a Fernet token at all.
"""


@dataclass
class Tally:
    """What one purpose's columns turned out to hold."""

    readable: int = 0
    unreadable: int = 0
    plaintext: int = 0
    columns: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.readable + self.unreadable + self.plaintext


def encrypted_columns() -> list[tuple[str, str, str]]:
    """``(table, column, purpose)`` for every encrypted column in the models.

    Walked from the mapper registry rather than listed, so a new encrypted
    column is covered by this check the moment it is declared. A list here would
    be a second inventory to forget to extend, which is the bug class
    jrhahn/ai-trainer#754 was about.
    """
    return sorted({(table.name, column.name, purpose) for table, column, purpose in _columns()})


def _columns() -> list[tuple[Table, Column, str]]:
    """The same sweep, keeping the SQLAlchemy objects.

    :func:`encrypted_columns` returns names because that is what a report and a
    test want to compare. The query below wants the objects: selecting a
    ``Column`` off its ``Table`` means the identifiers are quoted by the dialect
    instead of formatted into a SQL string, so there is no interpolation to
    reason about even though these names come from the models and not from input.
    """
    found: list[tuple[Table, Column, str]] = []
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is None:
            continue
        for column in table.columns:
            if isinstance(column.type, EncryptedString):
                found.append((table, column, column.type.purpose))
    return sorted(found, key=lambda item: (item[0].name, item[1].name))


def _classify(value: str, purpose: str) -> str:
    """``readable``, ``unreadable`` or ``plaintext`` for one stored value."""
    keys = settings.encryption_keys_for(purpose)
    if not keys:
        # No key configured for this purpose at all. Whatever is stored is
        # plaintext by design, and a value that looks like a Fernet token means
        # the key that wrote it has been *removed* from the configuration —
        # which is the disaster, reported as such.
        return "unreadable" if value.startswith(_FERNET_PREFIX) else "plaintext"

    fernet = MultiFernet([Fernet(key.encode()) for key in keys])
    try:
        fernet.decrypt(value.encode())
    except InvalidToken:
        return "unreadable" if value.startswith(_FERNET_PREFIX) else "plaintext"
    return "readable"


async def tally() -> dict[str, Tally]:
    """Classify every stored value, grouped by purpose."""
    tallies: dict[str, Tally] = defaultdict(Tally)
    async with async_session_maker() as session:
        for table, column, purpose in _columns():
            tallies[purpose].columns.append(f"{table.name}.{column.name}")
            # ``type_coerce(column, Text)`` and not ``select(column)``, which is
            # the whole subtlety here. A result processor comes from the type of
            # the selected expression, so selecting the column itself runs
            # ``EncryptedString.process_result_value`` — the decryption this
            # script exists to *observe*. Measured: the same row reads back as
            # 'PLAINSECRET12345' through the column and as 'gAAAAAB…' through
            # this, so the naive form would have counted every healthy row as
            # plaintext and reported a successful restore as an unencrypted one.
            #
            # Coercing the type rather than formatting a SQL string keeps the
            # identifiers quoted by the dialect, and emits no CAST.
            result = await session.execute(
                select(type_coerce(column, Text)).where(column.is_not(None))
            )
            for (value,) in result:
                if value is None:
                    continue
                verdict = _classify(value, purpose)
                entry = tallies[purpose]
                setattr(entry, verdict, getattr(entry, verdict) + 1)
    return tallies


def report(tallies: dict[str, Tally]) -> int:
    """Print one line per purpose; return the exit status."""
    broken = 0
    width = max((len(p) for p in SecretPurpose.ALL), default=8)

    for purpose in SecretPurpose.ALL:
        entry = tallies.get(purpose, Tally())
        if entry.unreadable:
            broken += entry.unreadable
            verdict = f"UNREADABLE: {entry.unreadable} of {entry.total}"
        elif entry.total == 0:
            verdict = "no rows stored"
        elif entry.plaintext and not entry.readable:
            verdict = f"all {entry.plaintext} plaintext (no key configured)"
        else:
            verdict = f"{entry.readable} readable"
            if entry.plaintext:
                verdict += f", {entry.plaintext} plaintext (predate encryption)"
        print(f"  {purpose:<{width}}  {verdict}")

    missing = [p for p in SecretPurpose.ALL if p not in tallies]
    if missing:
        # A purpose with no column at all is a configuration/model mismatch, not
        # an empty table: encrypted_columns() walks the models, so a purpose it
        # never saw has nothing storing it.
        print(f"\n  no column stores: {', '.join(missing)}", file=sys.stderr)

    if broken:
        print(
            f"\nRestore is NOT complete: {broken} stored secrets cannot be "
            "decrypted with the keys this process has.\n"
            "The database came back; the keys that open it did not. Check that "
            "the secrets bundle restored into .env is the one belonging to this "
            "dump — scripts/restore.sh compares their fingerprints.",
            file=sys.stderr,
        )
        return 1

    print("\nEvery stored secret opens with the keys this process has.")
    return 0


def main() -> int:
    print(f"Verifying encrypted columns against {os.environ.get('APP_ENV', 'development')} keys:")
    return report(asyncio.run(tally()))


if __name__ == "__main__":
    raise SystemExit(main())
