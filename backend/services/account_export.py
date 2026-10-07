"""Everything stored about one account, as JSON (GDPR Art. 15 and 20).

ai-trainer-ops#6. The export is built from the schema rather than from a list of
tables, for the same reason the deletion test walks the schema: a hand-written
list is complete on the day it is written and quietly incomplete from the next
migration on. Every table whose rows belong to a user through a foreign key to
``users.id`` is in the export by construction, and
``tests/test_account_data_rights.py`` pins that.

What is left out is credentials, not data about the athlete: anything stored
encrypted (provider API keys, Strava and intervals.icu tokens, the TOTP secret)
and the three hashes that stand in for a secret. Exporting those would turn a
borrowed session into the account's keys, and a file the athlete forwards to
someone into the same.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Column, Table, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from database import Base

EXPORT_FORMAT = "ai-trainer-account-export"
EXPORT_VERSION = 1

# Hashes are stored as plain strings, so the type alone does not find them.
SECRET_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("users", "hashed_password"),
        ("trusted_devices", "token_hash"),
        ("totp_recovery_codes", "code_hash"),
    }
)


def is_secret(table: Table, column: Column) -> bool:
    return (
        isinstance(column.type, models.EncryptedString)
        or (table.name, column.name) in SECRET_COLUMNS
    )


def owner_column(table: Table) -> Column | None:
    """The column that ties *table*'s rows to a user, if there is one."""
    for fk in table.foreign_keys:
        if fk.column.table.name == models.User.__tablename__:
            return fk.parent
    return None


def user_tables() -> list[Table]:
    """Every table holding rows that belong to one user, in schema order."""
    return [t for t in Base.metadata.sorted_tables if owner_column(t) is not None]


def exported_columns(table: Table) -> list[Column]:
    return [c for c in table.columns if not is_secret(table, c)]


async def _rows(
    db: AsyncSession, table: Table, owner: Column, user_id: str
) -> list[dict[str, Any]]:
    columns = exported_columns(table)
    result = await db.execute(
        select(*columns)
        .where(owner == user_id)
        .order_by(*table.primary_key.columns)
    )
    return [dict(row._mapping) for row in result]


async def export_account(db: AsyncSession, user: models.User) -> dict[str, Any]:
    users = models.User.__table__
    account = await _rows(db, users, users.c.id, user.id)
    tables = {}
    for table in user_tables():
        tables[table.name] = await _rows(db, table, owner_column(table), user.id)
    return jsonable_encoder(
        {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
            "exported_at": datetime.now(timezone.utc),
            "account": account[0] if account else None,
            "tables": tables,
        }
    )
