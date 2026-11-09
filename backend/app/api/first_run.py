"""The first-start wizard: its status per step, and starting, skipping, finishing and restarting it."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.dependencies import BlueprintDirDependency, SessionDependency
from app.core.first_run import FirstRunService, FirstRunStatus, Step, StepState

router = APIRouter(prefix="/first-run", tags=["first-run"])


def get_first_run(request: Request) -> FirstRunService:
    service: FirstRunService = request.app.state.first_run
    return service


FirstRunDependency = Annotated[FirstRunService, Depends(get_first_run)]


class FirstRunStepsOut(BaseModel):
    connection: StepState
    project: StepState
    backups: StepState
    starter: StepState


class FirstRunOut(BaseModel):
    # Whether the app opens the wizard.
    show: bool
    started: bool
    finished: bool
    steps: FirstRunStepsOut


def status_out(status: FirstRunStatus) -> FirstRunOut:
    return FirstRunOut(
        show=status.show,
        started=status.started,
        finished=status.finished,
        steps=FirstRunStepsOut(
            connection=status.steps["connection"],
            project=status.steps["project"],
            backups=status.steps["backups"],
            starter=status.steps["starter"],
        ),
    )


@router.get("", response_model=FirstRunOut)
async def read_first_run(
    service: FirstRunDependency, session: SessionDependency, blueprint_dir: BlueprintDirDependency
) -> FirstRunOut:
    """Per step whether it is done, skipped or still to do, and whether to show the wizard."""
    return status_out(await service.status(session, blueprint_dir))


@router.post("/start", response_model=FirstRunOut)
async def start_first_run(
    service: FirstRunDependency, session: SessionDependency, blueprint_dir: BlueprintDirDependency
) -> FirstRunOut:
    """The wizard opened: it comes back until it is finished, also when left halfway."""
    service.start(session)
    return status_out(await service.status(session, blueprint_dir))


@router.post("/skip/{step}", response_model=FirstRunOut)
async def skip_first_run_step(
    step: Step, service: FirstRunDependency, session: SessionDependency, blueprint_dir: BlueprintDirDependency
) -> FirstRunOut:
    """Leave a step for later; it shows as skipped until what it sets up exists."""
    service.skip(session, step)
    return status_out(await service.status(session, blueprint_dir))


@router.post("/finish", response_model=FirstRunOut)
async def finish_first_run(
    service: FirstRunDependency, session: SessionDependency, blueprint_dir: BlueprintDirDependency
) -> FirstRunOut:
    """Close the wizard for good; Settings can open it again."""
    service.finish(session)
    return status_out(await service.status(session, blueprint_dir))


@router.post("/restart", response_model=FirstRunOut)
async def restart_first_run(
    service: FirstRunDependency, session: SessionDependency, blueprint_dir: BlueprintDirDependency
) -> FirstRunOut:
    """Open the wizard again, with nothing finished or skipped."""
    service.restart(session)
    return status_out(await service.status(session, blueprint_dir))
