"""Add origin column to metrics and logs tables

Revision ID: f85c23010814
Revises: c4a1f0d92b73
Create Date: 2026-10-14

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f85c23010814"
down_revision: Union[str, Sequence[str], None] = "c4a1f0d92b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite requires batch mode for ALTER TABLE operations including
    # adding columns with constraints. Use batch_alter_table for each table.
    for table in ("gpu_metrics", "cpu_metrics", "log_entries"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "origin",
                    sa.String(16),
                    nullable=False,
                    server_default="synthetic",
                )
            )
            batch_op.create_check_constraint(
                f"ck_{table}_origin", "origin IN ('synthetic','real')"
            )
            batch_op.create_index(f"ix_{table}_origin", ["origin"])


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("gpu_metrics", "cpu_metrics", "log_entries"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_origin")
            batch_op.drop_constraint(f"ck_{table}_origin", type_="check")
            batch_op.drop_column("origin")
