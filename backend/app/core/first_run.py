"""The first start: which of its steps are done, derived from what exists, and whether to show the wizard.

Only the wizard's own progress is stored (the `first_run` setting); whether a step is done follows from the
installation itself, so an upgraded installation is not asked again and a half-finished wizard resumes where it was.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backups.runs import read_destinations
from app.blueprint import starter
from app.blueprint.levels import environment_layer, global_layer
from app.core.settings import SettingDefinition, find_setting_row, resolve_setting, store_setting
from app.credentials.store import find_credential
from app.providers.registry import ProviderRegistry
from app.storage.models import Environment, Project

Step = Literal["connection", "project", "backups", "starter"]
StepState = Literal["done", "todo", "skipped"]
STEPS: tuple[Step, ...] = get_args(Step)
BACKUP_SCHEDULE_KEY = "backup_schedule"
# A sign-in found is remembered; a missing one is asked again after this long, since each check starts a CLI.
SIGN_IN_RECHECK_SECONDS = 30.0

# Not in the settings registry, so Settings neither lists it nor lets it be set by hand; only this module writes it.
FIRST_RUN_SETTING = SettingDefinition(
    key="first_run",
    label="First start",
    description="How far the first-start wizard got: started, finished and the steps skipped.",
    levels=frozenset({"global"}),
    default={"started": False, "finished": False, "skipped": []},
)


@dataclass(frozen=True)
class FirstRunFacts:
    """What the installation has, one fact per step."""

    # An environment has a stored model-provider key, or a subscription CLI reports a sign-in.
    connection: bool
    project: bool
    # A backup destination is set, or the backup schedule was saved switched on.
    backups: bool
    # The starter's core pack is installed in the global layer or an environment's layer.
    starter: bool

    def has(self, step: Step) -> bool:
        return {
            "connection": self.connection,
            "project": self.project,
            "backups": self.backups,
            "starter": self.starter,
        }[step]


@dataclass(frozen=True)
class FirstRunStored:
    started: bool = False
    finished: bool = False
    skipped: frozenset[Step] = frozenset()


@dataclass(frozen=True)
class FirstRunStatus:
    steps: dict[Step, StepState]
    started: bool
    finished: bool
    # Whether the app opens the wizard.
    show: bool


def first_run_status(facts: FirstRunFacts, stored: FirstRunStored) -> FirstRunStatus:
    """Per step done, skipped or to do, and whether to show the wizard.

    A done step stays done when it was skipped earlier. The wizard shows until it is finished, once it was started,
    or on an installation without a connection and without a project; one that has either is never sent there.
    """
    steps: dict[Step, StepState] = {
        step: "done" if facts.has(step) else "skipped" if step in stored.skipped else "todo" for step in STEPS
    }
    fresh = not facts.connection and not facts.project
    return FirstRunStatus(steps, stored.started, stored.finished, not stored.finished and (stored.started or fresh))


def parse_stored(value: Any) -> FirstRunStored:
    """The stored setting, ignoring what does not fit (it is written only here, but survives restores)."""
    if not isinstance(value, dict):
        return FirstRunStored()
    skipped = value.get("skipped")
    steps = frozenset(step for step in STEPS if isinstance(skipped, list) and step in skipped)
    return FirstRunStored(value.get("started") is True, value.get("finished") is True, steps)


def stored_value(stored: FirstRunStored) -> dict[str, Any]:
    return {
        "started": stored.started,
        "finished": stored.finished,
        "skipped": [step for step in STEPS if step in stored.skipped],
    }


def has_connection_key(session: Session, providers: ProviderRegistry) -> bool:
    kinds = {spec.credential_kind for spec in providers.specs if spec.credential_kind}
    environments = list(session.scalars(select(Environment.id)))
    return any(find_credential(session, environment, kind) is not None for environment in environments for kind in kinds)


def has_backups(session: Session) -> bool:
    if read_destinations(session):
        return True
    # The schedule is on by default, which says nothing about a choice; only a saved schedule that is on counts.
    row = find_setting_row(session, BACKUP_SCHEDULE_KEY, None)
    return row is not None and isinstance(row.value, dict) and row.value.get("enabled") is True


def has_starter(session: Session, blueprint_dir: Path) -> bool:
    layers = [global_layer(blueprint_dir)]
    layers += [environment_layer(blueprint_dir, environment) for environment in session.scalars(select(Environment))]
    return any(starter.installed_ref(layer, starter.CORE_PACK) is not None for layer in layers)


@dataclass
class SignInCache:
    """Whether any subscription connection reports a sign-in, without starting its CLI on every status read."""

    signed_in: bool = False
    checked_at: float | None = None

    async def read(self, providers: ProviderRegistry, now: float) -> bool:
        if self.signed_in or (self.checked_at is not None and now - self.checked_at < SIGN_IN_RECHECK_SECONDS):
            return self.signed_in
        for spec in providers.specs:
            if spec.signed_in is not None and await spec.signed_in():
                self.signed_in = True
                break
        self.checked_at = now
        return self.signed_in


@dataclass
class FirstRunService:
    """Reads the facts and the stored progress, and records the wizard's progress."""

    providers: ProviderRegistry
    sign_ins: SignInCache = field(default_factory=SignInCache)

    async def facts(self, session: Session, blueprint_dir: Path) -> FirstRunFacts:
        connection = has_connection_key(session, self.providers) or await self.sign_ins.read(
            self.providers, time.monotonic()
        )
        return FirstRunFacts(
            connection=connection,
            project=session.scalar(select(Project.id).limit(1)) is not None,
            backups=has_backups(session),
            starter=has_starter(session, blueprint_dir),
        )

    def stored(self, session: Session) -> FirstRunStored:
        return parse_stored(resolve_setting(session, FIRST_RUN_SETTING, None).value)

    async def status(self, session: Session, blueprint_dir: Path) -> FirstRunStatus:
        return first_run_status(await self.facts(session, blueprint_dir), self.stored(session))

    def save(self, session: Session, stored: FirstRunStored) -> None:
        store_setting(session, FIRST_RUN_SETTING, None, stored_value(stored))

    def start(self, session: Session) -> None:
        current = self.stored(session)
        self.save(session, FirstRunStored(True, current.finished, current.skipped))

    def skip(self, session: Session, step: Step) -> None:
        current = self.stored(session)
        self.save(session, FirstRunStored(True, current.finished, current.skipped | {step}))

    def finish(self, session: Session) -> None:
        current = self.stored(session)
        self.save(session, FirstRunStored(current.started, True, current.skipped))

    def restart(self, session: Session) -> None:
        """Open the wizard again from Settings: started, with nothing finished or skipped."""
        self.save(session, FirstRunStored(started=True))
