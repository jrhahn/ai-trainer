"""add the athlete motivation model — what the athlete is optimizing for

The coaching system assumed physiological performance was the objective. For
most athletes it is only the enabling factor: they train to ride technical
descents, to enjoy a multi-day adventure, to feel good outdoors. This table
records the objective itself (#562), so the planner can score against it (#564)
and the coach can explain itself in the athlete's own terms (#565).

``athlete_context.motivation_drivers`` was the closest thing that existed — a
flat JSON list of free-text drivers with no structure, no provenance and no
consumer that treated it as an objective. Its contents are carried over as
seeded secondary objectives (``user_set``: an athlete typed them) and the column
is dropped, so motivation has exactly one home.

Revision ID: 20260811_000001
Revises: 20260810_000001
Create Date: 2026-08-11 00:00:01
"""

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "20260811_000001"
down_revision = "20260810_000001"
branch_labels = None
depends_on = None


# Mirrors services.motivation_model.DEFAULT_WEIGHTS. Duplicated on purpose: a
# migration must keep producing the rows it produced on the day it ran, even
# after the application's defaults move on.
_DEFAULT_WEIGHTS = {
    "enjoyment": 0.25,
    "adaptation": 0.30,
    "consistency": 0.20,
    "health": 0.15,
    "race_performance": 0.10,
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = inspector.get_table_names()

    if "athlete_motivation_model" not in tables:
        op.create_table(
            "athlete_motivation_model",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id"),
                primary_key=True,
            ),
            sa.Column(
                "primary_objective", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "primary_objective_source",
                sa.String(length=20),
                nullable=False,
                server_default="inferred",
            ),
            sa.Column(
                "primary_objective_confidence",
                sa.Float(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "primary_objective_snippet",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column("secondary_objectives", sa.JSON(), nullable=False),
            sa.Column("constraints", sa.JSON(), nullable=False),
            sa.Column("utility_weights", sa.JSON(), nullable=False),
            sa.Column("pinned_weights", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "athlete_context" not in tables:
        return

    context_columns = {c["name"] for c in inspector.get_columns("athlete_context")}
    if "motivation_drivers" not in context_columns:
        return

    _backfill_from_motivation_drivers(bind)

    with op.batch_alter_table("athlete_context") as batch:
        batch.drop_column("motivation_drivers")


def _backfill_from_motivation_drivers(bind) -> None:
    """Seed one motivation model per athlete who had motivation drivers.

    The drivers become ``user_set`` secondary objectives: someone typed them, so
    a later inference pass must not silently replace them. They are deliberately
    not promoted to a primary objective — "MTB" is a preference, and guessing an
    objective from it is exactly the conflation #562 exists to end.
    """
    now = datetime.now(timezone.utc)
    rows = bind.execute(
        sa.text(
            "SELECT user_id, motivation_drivers FROM athlete_context "
            "WHERE motivation_drivers IS NOT NULL"
        )
    ).fetchall()

    existing = {
        row[0]
        for row in bind.execute(
            sa.text("SELECT user_id FROM athlete_motivation_model")
        ).fetchall()
    }

    for user_id, drivers in rows:
        if user_id in existing:
            continue
        if isinstance(drivers, str):
            try:
                drivers = json.loads(drivers)
            except (TypeError, ValueError):
                continue
        if not isinstance(drivers, list):
            continue

        entries = []
        seen = set()
        for driver in drivers:
            if not isinstance(driver, str):
                continue
            text = " ".join(driver.split())[:200]
            if not text or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            entries.append(
                {
                    "text": text,
                    "confidence": 1.0,
                    "source": "user_set",
                    "source_snippet": "",
                    "status": "active",
                    "contradiction_note": None,
                }
            )
            if len(entries) >= 6:
                break

        if not entries:
            continue

        bind.execute(
            sa.text(
                "INSERT INTO athlete_motivation_model ("
                "  user_id, primary_objective, primary_objective_source,"
                "  primary_objective_confidence, primary_objective_snippet,"
                "  secondary_objectives, constraints, utility_weights,"
                "  pinned_weights, updated_at"
                ") VALUES ("
                "  :user_id, '', 'inferred', 0, '',"
                "  :secondary, :constraints, :weights, :pinned, :updated_at"
                ")"
            ).bindparams(
                sa.bindparam("secondary", type_=sa.JSON()),
                sa.bindparam("constraints", type_=sa.JSON()),
                sa.bindparam("weights", type_=sa.JSON()),
                sa.bindparam("pinned", type_=sa.JSON()),
            ),
            {
                "user_id": user_id,
                "secondary": entries,
                "constraints": [],
                "weights": _DEFAULT_WEIGHTS,
                "pinned": [],
                "updated_at": now,
            },
        )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    tables = inspector.get_table_names()

    if "athlete_context" in tables:
        columns = {c["name"] for c in inspector.get_columns("athlete_context")}
        if "motivation_drivers" not in columns:
            with op.batch_alter_table("athlete_context") as batch:
                batch.add_column(
                    sa.Column(
                        "motivation_drivers",
                        sa.JSON(),
                        nullable=False,
                        server_default="[]",
                    )
                )

    if "athlete_motivation_model" in tables:
        op.drop_table("athlete_motivation_model")
