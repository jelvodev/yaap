from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.api.schemas import EnvironmentCreate, EnvironmentOut, EnvironmentUpdate
from app.environments.service import create_environment as create_environment_row
from app.environments.service import find_environment_by_name, list_environments
from app.storage.models import Environment

router = APIRouter(prefix="/environments", tags=["environments"])


def environment_to_out(environment: Environment) -> EnvironmentOut:
    return EnvironmentOut(
        id=environment.id,
        name=environment.name,
        colour=environment.colour.upper(),
    )


@router.get("", response_model=list[EnvironmentOut])
def get_environments(session: SessionDependency) -> list[EnvironmentOut]:
    return [environment_to_out(environment) for environment in list_environments(session)]


@router.post("", response_model=EnvironmentOut, status_code=status.HTTP_201_CREATED)
def create_environment(payload: EnvironmentCreate, session: SessionDependency) -> EnvironmentOut:
    name = payload.name.strip()
    if find_environment_by_name(session, name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"An environment named '{name}' already exists.")
    environment = create_environment_row(session, name, payload.colour.upper())
    session.commit()
    return environment_to_out(environment)


@router.patch("/{environment_id}", response_model=EnvironmentOut)
def update_environment(
    payload: EnvironmentUpdate, environment: EnvironmentDependency, session: SessionDependency
) -> EnvironmentOut:
    if payload.name is not None:
        name = payload.name.strip()
        existing = find_environment_by_name(session, name)
        if existing and existing.id != environment.id:
            raise HTTPException(status.HTTP_409_CONFLICT, f"An environment named '{name}' already exists.")
        environment.name = name
    if payload.colour is not None:
        environment.colour = payload.colour.upper()
    session.commit()
    return environment_to_out(environment)
