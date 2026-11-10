"""The models YAAP downloads into its data folder: their states, downloading and removing them."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.local_models import ModelBusy, ModelService, ModelState, ModelStatus, UnknownModel

router = APIRouter(prefix="/local-models", tags=["local-models"])


def get_models(request: Request) -> ModelService:
    service: ModelService = request.app.state.models
    return service


ModelsDependency = Annotated[ModelService, Depends(get_models)]


class LocalModelOut(BaseModel):
    name: str
    label: str
    # What the model is for.
    purpose: str
    state: ModelState
    # Bytes in total and bytes downloaded so far.
    size: int
    downloaded: int
    message: str


def local_model_out(item: ModelStatus) -> LocalModelOut:
    return LocalModelOut(**item.__dict__)


@router.get("", response_model=list[LocalModelOut])
def list_models(models: ModelsDependency) -> list[LocalModelOut]:
    """The state of every model YAAP can download."""
    return [local_model_out(item) for item in models.statuses()]


@router.post("/{name}/download", response_model=LocalModelOut)
def download_model(name: str, models: ModelsDependency) -> LocalModelOut:
    """Start downloading a model in the background; the list shows its progress."""
    try:
        return local_model_out(models.start_download(name))
    except UnknownModel as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found.") from error


@router.delete("/{name}", response_model=LocalModelOut)
def remove_model(name: str, models: ModelsDependency) -> LocalModelOut:
    """Remove the model's files from the data folder; it can be downloaded again."""
    try:
        return local_model_out(models.remove(name))
    except UnknownModel as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found.") from error
    except ModelBusy as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
