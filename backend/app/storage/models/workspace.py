"""Environments, projects, settings and credentials."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    # Folder name under /workspace and /blueprint. Set once at creation; renaming the environment keeps it.
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    colour: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("environment_id", "slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80))
    # Working folder relative to /workspace, for example "work/website". Set once at creation.
    folder: Mapped[str] = mapped_column(String(200), unique=True)
    repository_url: Mapped[str | None] = mapped_column(String(400))
    # What a new conversation in this project starts with, by name; empty means the environment's default.
    default_agent: Mapped[str | None] = mapped_column(String(80))
    default_preset: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Setting(Base):
    """A setting value at one level: global (no environment, no project), one environment, or one project."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("environment_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    kind: Mapped[str] = mapped_column(String(40))
    encrypted_value: Mapped[bytes]
    last_four: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    last_tested_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_test_result: Mapped[str | None] = mapped_column(Text)
