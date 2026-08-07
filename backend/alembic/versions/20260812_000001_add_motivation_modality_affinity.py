"""add per-modality affinity to the athlete motivation model

The utility weights from #562 say *what* an athlete values; they cannot say in
what form. Two athletes can both weight enjoyment at 0.45 and mean completely
different rides by it — one wants technical singletrack, the other wants four
quiet hours on the road. The planner (#564) needs that difference to choose
between two options that are physiologically equivalent, which is the decision
the whole epic (#561) turns on.

Affinities are independent scores in [0, 1], not a distribution: liking the MTB
does not require disliking the road. Existing rows get the neutral 0.5 across
the board, which scores every modality equally and so changes nothing until the
behavioural pass (#563) has seen what the athlete actually rides.

Revision ID: 20260812_000001
Revises: 20260811_000001
Create Date: 2026-08-12 00:00:01
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "20260812_000001"
down_revision = "20260811_000001"
branch_labels = None
depends_on = None


# Mirrors services.motivation_model.DEFAULT_MODALITY_AFFINITY at the time this
# ran. Duplicated on purpose: a migration must keep producing the rows it
# produced on the day it ran, even after the application's defaults move on.
_NEUTRAL_AFFINITY = {
    "road": 0.5,
    "mtb": 0.5,
    "gravel": 0.5,
    "indoor": 0.5,
    "gym": 0.5,
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_motivation_model" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("athlete_motivation_model")}
    if "modality_affinity" in columns:
        return

    # Added nullable, backfilled, then made NOT NULL: an ALTER that rewrites
    # every row with a server default locks the table for the whole rewrite on
    # Postgres, and this column is JSON so there is no constant-default fast path.
    op.add_column(
        "athlete_motivation_model",
        sa.Column("modality_affinity", sa.JSON(), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE athlete_motivation_model SET modality_affinity = :neutral "
            "WHERE modality_affinity IS NULL"
        ).bindparams(sa.bindparam("neutral", type_=sa.JSON())),
        {"neutral": _NEUTRAL_AFFINITY},
    )
    with op.batch_alter_table("athlete_motivation_model") as batch:
        batch.alter_column(
            "modality_affinity",
            existing_type=sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{json.dumps(_NEUTRAL_AFFINITY)}'"),
        )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "athlete_motivation_model" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("athlete_motivation_model")}
    if "modality_affinity" not in columns:
        return
    with op.batch_alter_table("athlete_motivation_model") as batch:
        batch.drop_column("modality_affinity")
