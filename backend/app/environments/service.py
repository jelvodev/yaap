"""Environments: the top-level separation between, for example, work and private."""

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.presets.service import seed_presets
from app.storage.models import Environment

DEFAULT_ENVIRONMENTS = (
    ("Private", "#2563EB"),
    ("Work", "#FF6F00"),
)


def slugify(name: str, fallback: str) -> str:
    """A folder-safe name: lowercase letters, digits and hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-")
    return slug or fallback


def unique_environment_slug(session: Session, name: str) -> str:
    base = slugify(name, "environment")
    slug, counter = base, 2
    while session.scalar(select(Environment.id).where(Environment.slug == slug)) is not None:
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


def create_environment(session: Session, name: str, colour: str) -> Environment:
    environment = Environment(name=name, colour=colour, slug=unique_environment_slug(session, name))
    session.add(environment)
    session.flush()
    seed_presets(session, environment.id)
    session.flush()
    return environment


def seed_default_environments(session: Session) -> None:
    """Create the default environments on first start, so the application is usable right away."""
    if session.scalar(select(func.count()).select_from(Environment)):
        return
    for name, colour in DEFAULT_ENVIRONMENTS:
        create_environment(session, name, colour)
    session.commit()


def list_environments(session: Session) -> list[Environment]:
    return list(session.scalars(select(Environment).order_by(Environment.id)))


def find_environment_by_name(session: Session, name: str) -> Environment | None:
    return session.scalar(select(Environment).where(func.lower(Environment.name) == name.lower()))
