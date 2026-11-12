"""The evidence of a card's review as the API shows it: per acceptance criterion its status and source, the checks
that ran, the diff's size, the build's trace and screenshots (app.reviews.evidence)."""

from pydantic import BaseModel

from app.reviews.evidence import (
    CheckOutcome,
    CriterionStatus,
    Evidence,
    How,
    ScreenshotLabel,
    SourceKind,
    evidence_from_json,
)


class EvidenceSourceOut(BaseModel):
    kind: SourceKind
    # `check:<n>` (the n-th check that ran), `step:<n>` or `final:<n>` (the plan's checks), `<path>:<line>` (the file
    # the reviewer relied on) or a screenshot's reference.
    ref: str


class CriterionEvidenceOut(BaseModel):
    number: int
    text: str
    status: CriterionStatus
    how: How
    source: EvidenceSourceOut | None
    note: str


class CheckRunOut(BaseModel):
    command: str
    outcome: CheckOutcome
    step: str
    ref: str
    output: str


class DiffStatOut(BaseModel):
    files: int
    insertions: int
    deletions: int


class TraceRefOut(BaseModel):
    conversation_id: int
    run_id: int | None


class ScreenshotOut(BaseModel):
    label: ScreenshotLabel
    ref: str


class EvidenceOut(BaseModel):
    criteria: list[CriterionEvidenceOut]
    checks_run: list[CheckRunOut]
    diff_stat: DiffStatOut
    trace_ref: TraceRefOut | None
    screenshots: list[ScreenshotOut]
    notes: list[str]


def evidence_out(value: object) -> EvidenceOut | None:
    """The stored evidence of a review, or None for a review without one."""
    evidence = evidence_from_json(value)
    return None if evidence is None else from_evidence(evidence)


def from_evidence(evidence: Evidence) -> EvidenceOut:
    return EvidenceOut(
        criteria=[
            CriterionEvidenceOut(
                number=item.number,
                text=item.text,
                status=item.status,
                how=item.how,
                source=None if item.source is None else EvidenceSourceOut(kind=item.source.kind, ref=item.source.ref),
                note=item.note,
            )
            for item in evidence.criteria
        ],
        checks_run=[
            CheckRunOut(command=run.command, outcome=run.outcome, step=run.step, ref=run.ref, output=run.output)
            for run in evidence.checks_run
        ],
        diff_stat=DiffStatOut(
            files=evidence.diff_stat.files,
            insertions=evidence.diff_stat.insertions,
            deletions=evidence.diff_stat.deletions,
        ),
        trace_ref=None
        if evidence.trace_ref is None
        else TraceRefOut(conversation_id=evidence.trace_ref.conversation_id, run_id=evidence.trace_ref.run_id),
        screenshots=[ScreenshotOut(label=shot.label, ref=shot.ref) for shot in evidence.screenshots],
        notes=list(evidence.notes),
    )
