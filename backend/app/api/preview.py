"""The preview of a project's app: opening it on its own address, and a screenshot of it."""

import base64
from datetime import datetime

import anyio
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.conversations import ConversationDependency
from app.api.dependencies import ProjectDependency
from app.browser.service import save_shots
from app.mcp_client.runs import server_place
from app.preview.screenshot import ScreenshotError, browser_sees, conversation_browser, preview_url, screenshot_preview
from app.preview.service import PreviewError, PreviewService

router = APIRouter(tags=["preview"])


class PreviewLinkOut(BaseModel):
    # The preview's own address with an open token; valid once and for a few minutes.
    url: str
    process_id: int
    port: int
    expires_at: datetime


class PreviewScreenshotIn(BaseModel):
    process_id: int
    # A path on the dev server, such as /settings.
    path: str = Field(default="/", max_length=2000)


class PreviewScreenshotOut(BaseModel):
    # The number of the screenshot under /conversations/{id}/browser/shots/{number}.
    number: int
    url: str


@router.post("/projects/{project_id}/processes/{process_id}/preview", response_model=PreviewLinkOut)
def open_preview(project: ProjectDependency, process_id: int, request: Request) -> PreviewLinkOut:
    """A link that opens the preview of a running process with a port, scoped to that process."""
    preview: PreviewService = request.app.state.preview
    try:
        link = preview.link(process_id, project.id, request.headers.get("host"))
    except PreviewError as error:
        code = status.HTTP_404_NOT_FOUND if error.missing else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(error)) from error
    return PreviewLinkOut(url=link.url, process_id=link.process_id, port=link.port, expires_at=link.expires_at)


@router.post("/conversations/{conversation_id}/preview/screenshot", response_model=PreviewScreenshotOut)
async def preview_screenshot(
    conversation: ConversationDependency, payload: PreviewScreenshotIn, request: Request
) -> PreviewScreenshotOut:
    """A screenshot of a process's dev server through the conversation's browser, kept with its other screenshots."""
    preview: PreviewService = request.app.state.preview
    managed = preview.running(payload.process_id)
    if conversation.project_id is None or managed is None or managed.project_id != conversation.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No running process with a port and this id in the project.")
    command: list[str] | None = request.app.state.browser_command
    if command is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The agent's browser is not available.")
    if not browser_sees(managed):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The agent's browser runs in the runner and cannot see this process's machine."
        )
    data_dir = request.app.state.data_dir
    place = server_place(conversation.environment_id).place
    try:
        url = preview_url(managed.port or 0, payload.path)
        browser = conversation_browser(request.app.state.mcp, command, data_dir, conversation.id, place)
        shot = await screenshot_preview(browser, url)
    except ScreenshotError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    image = ((shot.media_type, base64.b64encode(shot.data).decode("ascii")),)
    (number,) = await anyio.to_thread.run_sync(save_shots, data_dir, conversation.id, image)
    return PreviewScreenshotOut(number=number, url=url)
