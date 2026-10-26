"""Move every stored secret onto its purpose's own key (ai-trainer-ops#299).

Since #12 each secret type has a key of its own (STRAVA_TOKEN_ENCRYPTION_KEY,
INTERVALS_ENCRYPTION_KEY, AI_KEY_ENCRYPTION_KEY, TOTP_ENCRYPTION_KEY) in front of
the shared SECRETS_ENCRYPTION_KEY. New writes use the dedicated key, and a row
written before stays readable through the shared one. A row moves only when it
is next written: a Strava token on every refresh, a TOTP secret practically
never. So the shared key cannot be retired until every row has been rewritten.

This rewrites them with ``MultiFernet.rotate``: read with the purpose's whole
key chain, write with its first key. It is a dry run unless ``--commit``:

    docker compose exec -T backend python -m scripts.reencrypt_secrets
    docker compose exec -T backend python -m scripts.reencrypt_secrets --commit

Before ``--commit``: a fresh backup, and a rehearsal on a restored copy. After
it: ``python -m scripts.verify_restore``. The run is one transaction, and every
value it wrote is opened with its own key alone before anything is committed.
Right before the shared key goes, one more dry run should find nothing to move.

Left alone, and counted:
- a purpose without a dedicated key, whose first key is the shared one, so there
  is nowhere to move to;
- a value that was never encrypted, which predates the keys and needs none;
- a value no configured key opens. Then nothing is written at all, and the exit
  status is 1, as with verify_restore: a key is missing, and retiring another
  one is the last thing to do.

The app keeps running while this does. A row it rewrites in the meantime (a
token refresh) is not overwritten: the update only applies to the value that
was read.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Column, Table, Text, and_, select, type_coerce, update

from config import settings
from database import async_session_maker
from scripts.verify_restore import FERNET_PREFIX, encrypted_column_objects


@dataclass
class Count:
    """What one encrypted column held, and what became of it."""

    column: str
    purpose: str
    on_own_key: int = 0
    moved: int = 0
    changed_meanwhile: int = 0
    plaintext: int = 0
    unreadable: int = 0
    # Why the whole column was left alone, if it was.
    left_alone: str = ""


class Unreadable(Exception):
    """A stored value no configured key opens; nothing may be written."""


class CheckFailed(Exception):
    """A rewritten value did not open with its own key alone."""


def _chain(purpose: str) -> list[str] | None:
    """The purpose's keys, its own first, or ``None`` when it has no key of its own."""
    keys = settings.encryption_keys_for(purpose)
    if not keys or keys[0] == settings.encryption_key:
        return None
    return keys


async def _move(
    session, table: Table, column: Column, ids: list, old: str, new: str
) -> bool:
    """Write ``new`` over ``old`` in the row with primary key ``ids``.

    The stored value is compared and written as text, past the column's own
    type: through it, the token would be encrypted a second time. And only if
    it is still ``old``: the app may have rewritten the row since it was read.
    """
    key = list(table.primary_key.columns)
    result = await session.execute(
        update(table)
        .where(and_(*(part == value for part, value in zip(key, ids))))
        .where(type_coerce(column, Text) == old)
        .values({column.name: type_coerce(new, Text)})
    )
    return result.rowcount == 1


async def run(*, commit: bool) -> list[Count]:
    """Count, and with ``commit`` rewrite, every encrypted column's values.

    Two passes in one transaction. The first reads and sorts every value, so a
    value no key opens stops the run before anything is written or locked
    (review of #1147). The second writes the moves, reads each back, and only
    then commits.
    """
    counts: list[Count] = []
    pending: list[tuple[Count, Table, Column, list, str, str, Fernet, MultiFernet]] = []
    async with async_session_maker() as session:
        for table, column, purpose in encrypted_column_objects():
            count = Count(f"{table.name}.{column.name}", purpose)
            counts.append(count)
            keys = _chain(purpose)
            if keys is None:
                count.left_alone = "no key of its own"
                continue
            own = Fernet(keys[0].encode())
            chain = MultiFernet([Fernet(key.encode()) for key in keys])
            key = list(table.primary_key.columns)
            rows = await session.execute(
                select(*key, type_coerce(column, Text)).where(column.is_not(None))
            )
            for *ids, value in rows.all():
                try:
                    own.decrypt(value.encode())
                except InvalidToken:
                    pass
                else:
                    count.on_own_key += 1
                    continue
                try:
                    token = chain.rotate(value.encode()).decode()
                except InvalidToken:
                    if value.startswith(FERNET_PREFIX):
                        count.unreadable += 1
                    else:
                        count.plaintext += 1
                    continue
                pending.append((count, table, column, ids, value, token, own, chain))
        unreadable = any(count.unreadable for count in counts)
        if not commit or unreadable:
            # What would move, also when nothing may (review of #1147).
            for count, *_ in pending:
                count.moved += 1
            if unreadable and commit:
                raise Unreadable(counts)
            return counts
        written = []
        for count, table, column, ids, value, token, own, chain in pending:
            if await _move(session, table, column, ids, value, token):
                count.moved += 1
                written.append((table, column, ids, value, token, own, chain))
            else:
                count.changed_meanwhile += 1
        # Each secret is decrypted for one comparison and dropped, not held for
        # the whole run (review of #1147).
        for table, column, ids, old, token, own, chain in written:
            stored = await session.scalar(
                select(type_coerce(column, Text)).where(
                    and_(
                        *(
                            part == value
                            for part, value in zip(table.primary_key.columns, ids)
                        )
                    )
                )
            )
            if stored != token or own.decrypt(stored.encode()) != chain.decrypt(
                old.encode()
            ):
                await session.rollback()
                raise CheckFailed(f"{table.name}.{column.name} {ids}")
        await session.commit()
    return counts


def report(counts: list[Count], *, commit: bool) -> None:
    width = max((len(count.column) for count in counts), default=10)
    for count in counts:
        if count.left_alone:
            line = f"left alone: {count.left_alone}"
        else:
            parts = [
                f"{count.moved} {'moved' if commit else 'to move'}",
                f"{count.on_own_key} on its own key",
            ]
            if count.changed_meanwhile:
                parts.append(
                    f"{count.changed_meanwhile} rewritten by the app meanwhile"
                )
            if count.plaintext:
                parts.append(f"{count.plaintext} plaintext (predate encryption)")
            if count.unreadable:
                parts.append(f"{count.unreadable} UNREADABLE")
            line = ", ".join(parts)
        print(f"  {count.column:<{width}}  {count.purpose:<9}  {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commit",
        action="store_true",
        help="write the moves; without it nothing is written",
    )
    commit = parser.parse_args(argv).commit
    print(
        "Moving stored secrets onto their own keys"
        + ("" if commit else " (dry run, nothing is written)")
        + ":"
    )
    try:
        counts = asyncio.run(run(commit=commit))
    except Unreadable as error:
        report(error.args[0], commit=False)
        print(
            "\nNothing was written: some stored secrets open with no configured key. "
            "Find the key that wrote them first (scripts.verify_restore names the purpose).",
            file=sys.stderr,
        )
        return 1
    except CheckFailed as error:
        print(
            f"\nNothing was written: {error} did not open with its own key after the rewrite.",
            file=sys.stderr,
        )
        return 1
    report(counts, commit=commit)
    if any(count.unreadable for count in counts):
        print(
            "\nSome stored secrets open with no configured key; --commit would write nothing.",
            file=sys.stderr,
        )
        return 1
    moved = sum(count.moved for count in counts)
    if commit:
        print(
            f"\nMoved {moved}, each opened with its own key before the commit. Now run python -m scripts.verify_restore."
        )
    else:
        print(f"\n{moved} would move. Run again with --commit to write them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
