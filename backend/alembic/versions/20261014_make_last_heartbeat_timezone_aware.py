"""Make last_heartbeat timezone-aware (UTC)

Revision ID: 20261014_make_last_heartbeat_timezone_aware
Revises: f85c23010814
Create Date: 2026-10-14

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261014_make_last_heartbeat_timezone_aware"
down_revision: str = "f85c23010814"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema: make last_heartbeat timezone-aware."""
    # On SQLite, ALTER COLUMN TYPE is not supported. Since SQLite has no timezone
    # type at storage level, this is a metadata-only change for SQLite. We use
    # batch_alter_table which handles SQLite's ALTER TABLE limitations by
    # recreating the table. For other dialects, the direct alter_column works.
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("nodes") as batch_op:
            batch_op.alter_column(
                "last_heartbeat",
                existing_type=sa.DateTime(),
                type_=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "nodes",
            "last_heartbeat",
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema: revert last_heartbeat to timezone-naive."""
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("nodes") as batch_op:
            batch_op.alter_column(
                "last_heartbeat",
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime(),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "nodes",
            "last_heartbeat",
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=False,
        )
