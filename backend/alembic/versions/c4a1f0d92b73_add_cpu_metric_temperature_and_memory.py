"""add temperature and memory used to cpu metrics

Revision ID: c4a1f0d92b73
Revises: 9e9891b90d08
Create Date: 2026-09-30

The frontend and openapi.yaml both treat `temperature_c` and
`memory_used_gb` as required members of a CPU metric sample, but the
backend never produced them, so those columns rendered as undefined in
the CPU chart and table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a1f0d92b73"
down_revision: Union[str, Sequence[str], None] = "9e9891b90d08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot ALTER a column, but it can add a NOT NULL column that has
    # a server default - existing rows are filled in with that default. So the
    # columns are added already-constrained and then backfilled with real
    # values, which avoids any ALTER TABLE.
    op.add_column(
        "cpu_metrics",
        sa.Column(
            "temperature_c",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "cpu_metrics",
        sa.Column(
            "memory_used_gb",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )

    # Backfill from the percentage columns that are already stored, using the
    # same 32 GiB assumption the generators use.
    op.execute(
        "UPDATE cpu_metrics SET temperature_c = "
        "CAST(ROUND(45.0 + cpu_percent * 0.35) AS INTEGER) "
        "WHERE temperature_c = 0"
    )
    op.execute(
        "UPDATE cpu_metrics SET memory_used_gb = "
        "ROUND((32.0 * memory_percent) / 100.0, 2) "
        "WHERE memory_used_gb = 0.0"
    )


def downgrade() -> None:
    op.drop_column("cpu_metrics", "memory_used_gb")
    op.drop_column("cpu_metrics", "temperature_c")
