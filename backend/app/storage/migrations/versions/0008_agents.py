"""Agents: the agent of a conversation, project defaults, and stored agent runs.

Revision ID: 0008_agents
Revises: 0007_model_presets
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_agents"
down_revision = "0007_model_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("agent", sa.String(80)))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("default_agent", sa.String(80)))
        batch.add_column(sa.Column("default_preset", sa.String(40)))
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("messages.id")),
        sa.Column("agent", sa.String(80)),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tool_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approvals", sa.JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_runs_environment_id", "agent_runs", ["environment_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_conversation_id", "agent_runs")
    op.drop_index("ix_agent_runs_environment_id", "agent_runs")
    op.drop_table("agent_runs")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("default_preset")
        batch.drop_column("default_agent")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("agent")
