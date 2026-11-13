"""How the reply stream is described in the API: server-sent events, each one `ReplyEvent`."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import RootModel

from app.core import reply_event_models


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


class ReplyEvent(RootModel[reply_event_models.ReplyEvent]):
    """One event of a reply stream: the `event:` line names it, the `data:` line holds it as JSON."""


def reply_stream_responses(description: str) -> dict[int | str, dict[str, Any]]:
    """The `responses` of an endpoint that streams a reply, with its events in the API description."""
    return {200: {"model": ReplyEvent, "description": description}}


def event_stream(events: AsyncIterator[str]) -> EventStreamResponse:
    return EventStreamResponse(events, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
