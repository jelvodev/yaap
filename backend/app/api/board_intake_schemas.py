"""Request and answer shapes of the board's intake routes: importing GitHub issues and a card from a review's
finding."""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.board.issues import MAX_IMPORT, ImportResult, SkipReason


class IssueImport(BaseModel):
    """Which issues to import: `numbers`, or the open issues with `label`; one of the two."""

    numbers: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=MAX_IMPORT)
    label: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_way(self) -> "IssueImport":
        if bool(self.numbers) == (self.label is not None):
            raise ValueError("Give issue numbers or a label, not both.")
        return self


class ImportedIssueOut(BaseModel):
    number: int
    card_id: int
    title: str


class SkippedIssueOut(BaseModel):
    number: int
    # imported (a card exists already: card_id), pull_request, not_found, or limit (Inbox is full)
    reason: SkipReason
    card_id: int | None = None


class IssueImportOut(BaseModel):
    repository: str
    added: list[ImportedIssueOut]
    skipped: list[SkippedIssueOut]


class FindingCard(BaseModel):
    """*Make a card* on a review's finding: the review and the finding's place in its list."""

    review_id: int = Field(ge=1)
    finding: int = Field(ge=0)


def import_out(result: ImportResult) -> IssueImportOut:
    return IssueImportOut(
        repository=result.repository,
        added=[ImportedIssueOut(number=item.number, card_id=item.card_id, title=item.title) for item in result.added],
        skipped=[
            SkippedIssueOut(number=item.number, reason=item.reason, card_id=item.card_id) for item in result.skipped
        ],
    )
