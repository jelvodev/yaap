"""The vault: one store for keys and secrets.

Copies every credential (as YAAP's own key, the default of its kind) and every secret (as an entry the agent may use)
into vault_entries. The old tables stay in place, unused; removing them is a separate step that needs approval.

Revision ID: 0027_vault
Revises: 0026_conversation_access
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_vault"
down_revision = "0026_conversation_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vault_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("agent_use", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("encrypted_value", sa.LargeBinary, nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False, server_default=""),
        sa.Column("public_fields", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False, server_default="builtin"),
        sa.Column("reference", sa.String(300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_result", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vault_entries_environment_id", "vault_entries", ["environment_id"])
    op.create_index("ix_vault_entries_project_id", "vault_entries", ["project_id"])
    op.create_table(
        "vault_uses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entry_id", sa.Integer, sa.ForeignKey("vault_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_by", sa.String(200), nullable=False),
        sa.Column("conversation_id", sa.Integer, nullable=True),
    )
    op.create_index("ix_vault_uses_entry_id", "vault_uses", ["entry_id"])
    op.create_index("ix_vault_uses_used_at", "vault_uses", ["used_at"])
    op.execute(
        "INSERT INTO vault_entries (environment_id, project_id, name, kind, agent_use, is_default, encrypted_value, "
        "last_four, public_fields, description, source, last_tested_at, last_test_result, created_at, updated_at) "
        "SELECT environment_id, NULL, 'default', kind, 0, 1, encrypted_value, last_four, '{}', '', 'builtin', "
        "last_tested_at, last_test_result, created_at, updated_at FROM credentials"
    )
    op.execute(
        "INSERT INTO vault_entries (environment_id, project_id, name, kind, agent_use, is_default, encrypted_value, "
        "last_four, public_fields, description, source, created_at, updated_at) "
        "SELECT environment_id, project_id, name, 'generic', 1, 0, encrypted_value, '', '{}', '', 'builtin', "
        "created_at, updated_at FROM secrets"
    )


def downgrade() -> None:
    op.drop_table("vault_uses")
    op.drop_table("vault_entries")
