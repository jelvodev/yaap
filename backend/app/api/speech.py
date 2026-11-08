"""Speech: the local transcription model, transcribing a recording, and a reply's text to read aloud."""

import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.core.settings import resolve_languages
from app.speech.languages import guess_language, language_code
from app.speech.reading import reply_text, speakable_text
from app.speech.service import SpeechNotReady, SpeechService, SpeechStatus
from app.speech.whisper import AudioError, model_for
from app.storage.models import Conversation, Message, Project

router = APIRouter(tags=["speech"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Two first requests at once must not make two services, which could download the model twice.
SERVICE_LOCK = threading.Lock()


class SpeechStatusOut(BaseModel):
    state: Literal["ready", "missing", "downloading", "failed", "unavailable"]
    # The model for this level: small.en when replies are in English, otherwise the multilingual small.
    model: str
    size: int
    downloaded: int
    # The ISO 639-1 code transcription uses, from the reply language; None: Whisper detects the language.
    language: str | None
    message: str


class TranscriptionOut(BaseModel):
    text: str
    # The language Whisper heard (or was told).
    language: str | None
    seconds: float
    model: str


class SpeechTextOut(BaseModel):
    # The reply as plain sentences: no thinking, tool calls, code blocks, images or addresses.
    text: str
    # ISO 639-1 code for choosing a voice: the reply language, or guessed from the text; None: the browser's default.
    language: str | None


def speech_service(request: Request) -> SpeechService:
    """The application's speech service, made on first use with its models under <data>/models/whisper."""
    with SERVICE_LOCK:
        service = getattr(request.app.state, "speech", None)
        if service is None:
            service = SpeechService(Path(request.app.state.data_dir) / "models" / "whisper")
            request.app.state.speech = service
    return service


def reply_language_code(session: Session, environment_id: int, project_id: int | None) -> str | None:
    reply = resolve_languages(session, environment_id, project_id).reply
    return language_code(reply) if reply else None


def checked_project(session: Session, environment_id: int, project_id: int | None) -> int | None:
    if project_id is None:
        return None
    project = session.get(Project, project_id)
    if project is None or project.environment_id != environment_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return project.id


def status_out(status_value: SpeechStatus, language: str | None) -> SpeechStatusOut:
    return SpeechStatusOut(
        state=status_value.state,
        model=status_value.model,
        size=status_value.size,
        downloaded=status_value.downloaded,
        language=language,
        message=status_value.message,
    )


@router.get("/environments/{environment_id}/speech", response_model=SpeechStatusOut)
def get_speech_status(
    request: Request, environment: EnvironmentDependency, session: SessionDependency, project_id: int | None = None
) -> SpeechStatusOut:
    """Whether the transcription model for this level is ready, missing, downloading, failed or unavailable."""
    language = reply_language_code(session, environment.id, checked_project(session, environment.id, project_id))
    return status_out(speech_service(request).status(model_for(language)), language)


@router.post("/environments/{environment_id}/speech/download", response_model=SpeechStatusOut)
def download_speech_model(
    request: Request, environment: EnvironmentDependency, session: SessionDependency, project_id: int | None = None
) -> SpeechStatusOut:
    """Start downloading the transcription model in the background; the status shows its progress."""
    language = reply_language_code(session, environment.id, checked_project(session, environment.id, project_id))
    return status_out(speech_service(request).start_download(model_for(language)), language)


@router.post("/environments/{environment_id}/speech/transcriptions", response_model=TranscriptionOut)
def transcribe(
    request: Request,
    audio: UploadFile,
    environment: EnvironmentDependency,
    session: SessionDependency,
    project_id: int | None = None,
) -> TranscriptionOut:
    """A recording as text, transcribed on this computer. The text is for the user to review, not sent by itself."""
    language = reply_language_code(session, environment.id, checked_project(session, environment.id, project_id))
    data = audio.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The recording is larger than 25 MB.")
    service = speech_service(request)
    name = model_for(language)
    try:
        result = service.transcribe(name, data, language)
    except SpeechNotReady as error:
        if error.status.state == "unavailable":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, error.status.message) from error
        # First use: the model is fetched now; the browser shows the progress and the user records again.
        current = service.start_download(name)
        raise HTTPException(status.HTTP_409_CONFLICT, current.message) from error
    except AudioError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return TranscriptionOut(text=result.text, language=result.language, seconds=result.seconds, model=result.model)


@router.get("/messages/{message_id}/speech", response_model=SpeechTextOut)
def message_speech(message_id: int, session: SessionDependency) -> SpeechTextOut:
    """The text of a reply to read aloud with the browser's voices, and its language."""
    message = session.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reply not found.")
    conversation = session.get_one(Conversation, message.conversation_id)
    text = speakable_text(reply_text(message.content))
    language = reply_language_code(session, conversation.environment_id, conversation.project_id)
    return SpeechTextOut(text=text, language=language or guess_language(text))
