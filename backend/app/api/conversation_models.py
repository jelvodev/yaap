"""Request and response models of the conversation endpoints that no other module shares."""

from typing import Literal

from pydantic import BaseModel, Field

from app.core.summaries import MAX_FOCUS_CHARS


class RuleProposalOut(BaseModel):
    rule: str
    level: Literal["global", "environment", "project"]
    path: str | None
    # False when the utility model was not available and the proposal is the user's own text.
    from_model: bool
    # Why no model drafted it, when the model policy skipped the task.
    skipped: str | None = None


class CompactIn(BaseModel):
    # What the summary should keep in detail, as with /compact <focus>.
    focus: str | None = Field(default=None, max_length=MAX_FOCUS_CHARS)


class SummaryOut(BaseModel):
    # The last message the summary covers, and how many messages of the branch it covers.
    message_id: int
    covered: int
