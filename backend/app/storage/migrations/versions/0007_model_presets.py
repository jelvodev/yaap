"""Model presets per environment, seeded with Fast, Balanced, Deep and Cheap.

Revision ID: 0007_model_presets
Revises: 0006_attachments
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_model_presets"
down_revision = "0006_attachments"
branch_labels = None
depends_on = None

DEFAULT_PRESETS = (
    ("Fast", "claude-sonnet-5-5", "low", False),
    ("Balanced", "claude-opus-5-5", "medium", True),
    ("Deep", "claude-opus-5-5", "high", False),
    ("Cheap", "claude-haiku-5-5", "low", False),
)


def upgrade() -> None:
    presets = op.create_table(
        "model_presets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("effort", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("environment_id", "name"),
    )
    op.create_index("ix_model_presets_environment_id", "model_presets", ["environment_id"])
    environment_ids = [row[0] for row in op.get_bind().execute(sa.text("SELECT id FROM environments"))]
    op.bulk_insert(
        presets,
        [
            {
                "environment_id": environment_id,
                "name": name,
                "model": model,
                "effort": effort,
                "position": position,
                "is_default": is_default,
            }
            for environment_id in environment_ids
            for position, (name, model, effort, is_default) in enumerate(DEFAULT_PRESETS)
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_model_presets_environment_id", "model_presets")
    op.drop_table("model_presets")
