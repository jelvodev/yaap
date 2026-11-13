"""The events of a reply stream, one model each, joined in the `ReplyEvent` union.

Each event goes out as a server-sent event: its name on the `event:` line and the model as JSON (the name included)
on the `data:` line. The union is published in the API description, so the browser reads the stream through
generated types.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.conversation_schemas import ConversationSummaryOut, MessageOut
from app.core.utility_skips import UtilitySkipOut
from app.core.why import WhyOut
from app.tracing.route_schema import RouteStopOut

ApprovalDecision = Literal["approve", "deny"]

# The event name has a default, so it is never written out by hand; the published schema still lists it as always
# present, so the browser's types can switch on it.
ALWAYS_SENT = ConfigDict(json_schema_serialization_defaults_required=True)


class EventModel(BaseModel):
    model_config = ALWAYS_SENT


class TextDeltaEvent(EventModel):
    event: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDeltaEvent(EventModel):
    event: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ToolCallEvent(EventModel):
    event: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any]
    # The key its checkpoint and live output carry; identical calls share it and are matched in order.
    call_key: str
    # A checkpoint is taken before the call.
    checkpointable: bool
    # The call writes project files itself, so a preview reloads after it succeeds.
    writes_files: bool


class ToolResultEvent(EventModel):
    event: Literal["tool_result"] = "tool_result"
    id: str
    output: str
    is_error: bool


class CallRestartedEvent(EventModel):
    """A model call broke off and starts again: the text and reasoning it streamed are void."""

    event: Literal["call_restarted"] = "call_restarted"
    attempt: int
    reason: str


class ToolOutputEvent(EventModel):
    """Output of a running command, as it comes."""

    event: Literal["tool_output"] = "tool_output"
    call_key: str
    text: str


class SteeringEvent(EventModel):
    """A steering message reached the agent with a tool result."""

    event: Literal["steering"] = "steering"
    call_key: str
    text: str


class CheckpointEvent(EventModel):
    event: Literal["checkpoint"] = "checkpoint"
    checkpoint_id: int
    call_key: str
    tool: str


class DelegateProgressEvent(EventModel):
    """A delegated sub-run reports which tool it is using."""

    event: Literal["delegate_progress"] = "delegate_progress"
    run_id: int
    label: str
    step: str


class PlanSubmittedEvent(EventModel):
    event: Literal["plan_submitted"] = "plan_submitted"
    plan_id: int


class ApprovalRequestEvent(EventModel):
    """An action of the run that waits for the user's decision."""

    event: Literal["approval_request"] = "approval_request"
    approval_id: str
    tool: str
    summary: str
    detail: str
    # Why a tool that normally runs by itself asks: the run read outside content.
    reason: str | None = None
    # What the optional utility-model check found suspicious.
    suspicion: str | None = None
    # The rule behind the question, when a rule decided it.
    why: WhyOut | None = None


class ApprovalResolvedEvent(EventModel):
    event: Literal["approval_resolved"] = "approval_resolved"
    approval_id: str
    decision: ApprovalDecision


class DocumentOpenEvent(EventModel):
    """The agent opened the shared document."""

    event: Literal["document_open"] = "document_open"
    path: str


class DocumentWritingEvent(EventModel):
    """The agent starts a change of the shared document."""

    event: Literal["document_writing"] = "document_writing"
    path: str


class DocumentChangeEvent(EventModel):
    """The agent's change of the shared document ended; hash is the document's version after it (None: gone)."""

    event: Literal["document_change"] = "document_change"
    path: str
    hash: str | None


class DocumentSeenEvent(EventModel):
    """The agent got the user's saved changes of the shared document with a tool result."""

    event: Literal["document_seen"] = "document_seen"
    call_key: str
    path: str


class UserMessageEvent(MessageOut):
    """The user's message as stored, first in every reply stream."""

    model_config = ALWAYS_SENT

    event: Literal["user_message"] = "user_message"


class DoneEvent(EventModel):
    """The reply is stored."""

    event: Literal["done"] = "done"
    message: MessageOut
    conversation: ConversationSummaryOut


class TitleEvent(EventModel):
    """A better title from the utility model, after the first reply."""

    event: Literal["title"] = "title"
    conversation: ConversationSummaryOut


class UtilitySkippedEvent(UtilitySkipOut):
    """The model policy skipped a utility task of this exchange."""

    model_config = ALWAYS_SENT

    event: Literal["utility_skipped"] = "utility_skipped"


class StageEvent(RouteStopOut):
    """A stop of the reply's route was reached or changed; the same id replaces the earlier stop."""

    model_config = ALWAYS_SENT

    event: Literal["stage"] = "stage"


class StageRemovedEvent(EventModel):
    """A live stop is gone again."""

    event: Literal["stage_removed"] = "stage_removed"
    id: str


class CompactedEvent(EventModel):
    """/compact summarised the conversation up to message_id."""

    event: Literal["compacted"] = "compacted"
    message_id: int
    covered: int
    focus: str | None


class CompactFailedEvent(EventModel):
    event: Literal["compact_failed"] = "compact_failed"
    detail: str


class EvalStartedEvent(EventModel):
    """/eval started an eval run instead of a reply."""

    event: Literal["eval_started"] = "eval_started"
    run_id: int
    project_id: int
    detail: str


# The events an agent run puts on its queue while the model streams; the chat passes them on.
RunEvent = (
    ToolOutputEvent
    | SteeringEvent
    | CheckpointEvent
    | DelegateProgressEvent
    | PlanSubmittedEvent
    | ApprovalRequestEvent
    | ApprovalResolvedEvent
    | DocumentOpenEvent
    | DocumentWritingEvent
    | DocumentChangeEvent
    | DocumentSeenEvent
)

ReplyEvent = Annotated[
    TextDeltaEvent
    | ReasoningDeltaEvent
    | ToolCallEvent
    | ToolResultEvent
    | CallRestartedEvent
    | RunEvent
    | UserMessageEvent
    | DoneEvent
    | TitleEvent
    | UtilitySkippedEvent
    | StageEvent
    | StageRemovedEvent
    | CompactedEvent
    | CompactFailedEvent
    | EvalStartedEvent,
    Field(discriminator="event"),
]

