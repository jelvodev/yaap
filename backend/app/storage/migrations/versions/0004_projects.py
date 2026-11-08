"""Projects with a working folder; environment folder names; conversations inside a project.

Revision ID: 0004_projects
Revises: 0003_usage_billing
Create Date: 2026-11-05
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "0004_projects"
down_revision = "0003_usage_billing"
branch_labels = None
depends_on = None


def slugify(name: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or fallback


def upgrade() -> None:
    with op.batch_alter_table("environments") as batch:
        batch.add_column(sa.Column("slug", sa.String(80)))

    connection = op.get_bind()
    taken: set[str] = set()
    for environment_id, name in connection.execute(sa.text("SELECT id, name FROM environments ORDER BY id")):
        slug = slugify(name, f"environment-{environment_id}")
        while slug in taken:
            slug = f"{slug}-{environment_id}"
        taken.add(slug)
        connection.execute(sa.text("UPDATE environments SET slug = :slug WHERE id = :id"), {"slug": slug, "id": environment_id})

    with op.batch_alter_table("environments") as batch:
        batch.alter_column("slug", existing_type=sa.String(80), nullable=False)
        batch.create_unique_constraint("uq_environments_slug", ["slug"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("folder", sa.String(200), nullable=False, unique=True),
        sa.Column("repository_url", sa.String(400)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment_id", "slug"),
    )

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer))
        batch.create_foreign_key("fk_conversations_project_id", "projects", ["project_id"], ["id"])
        batch.create_index("ix_conversations_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index("ix_conversations_project_id")
        batch.drop_constraint("fk_conversations_project_id", type_="foreignkey")
        batch.drop_column("project_id")
    op.drop_table("projects")
    with op.batch_alter_table("environments") as batch:
        batch.drop_constraint("uq_environments_slug", type_="unique")
        batch.drop_column("slug")
