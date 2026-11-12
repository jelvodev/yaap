"""The evidence of a card's review: per acceptance criterion what YAAP checked or the reviewer judged, and where that
shows, beside the checks that ran, the size of the diff, the build's trace and screenshots.

Everything here is pure. `assemble` puts YAAP's own checks together when the review starts; `with_judgements` adds the
reviewer's answers for the criteria without a check when it ends. A criterion is only `passed` or `failed` when YAAP
ran its check, only `judged_met` or `judged_not_met` when the reviewer said so and named what it relied on; everything
else is `not_checked`, never a guess. The stored form is `reviews.evidence`; `evidence_markdown` is the short summary
written as the card's `## Evidence`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args

from app.evals.models import CommandCheck, FileCheck
from app.plans.model import Plan, StepStatus
from app.plans.progress import FinalCheck
from app.specs.criteria import Criterion

CriterionStatus = Literal["passed", "failed", "judged_met", "judged_not_met", "not_checked"]
How = Literal["command", "file", "reviewer", "none"]
SourceKind = Literal["check_output", "run_step", "finding", "screenshot"]
Verdict = Literal["met", "not_met", "unclear"]
CheckOutcome = Literal["passed", "failed", "not_run", "stopped", "error"]
ScreenshotLabel = Literal["before", "after"]

STATUSES: tuple[CriterionStatus, ...] = get_args(CriterionStatus)
HOWS: tuple[How, ...] = get_args(How)
SOURCE_KINDS: tuple[SourceKind, ...] = get_args(SourceKind)
VERDICTS: tuple[Verdict, ...] = get_args(Verdict)
OUTCOMES: tuple[CheckOutcome, ...] = get_args(CheckOutcome)
LABELS: tuple[ScreenshotLabel, ...] = get_args(ScreenshotLabel)

NO_PREVIEW = "No preview for this project"
MAX_REASON = 500
MAX_OUTPUT = 2000


@dataclass(frozen=True)
class Source:
    """Where a criterion's evidence shows: `check:<n>` (the n-th check that ran), `step:<n>` (a plan step),
    `<path>:<line>` (the file the reviewer relied on) or a screenshot's reference."""

    kind: SourceKind
    ref: str


@dataclass(frozen=True)
class CriterionEvidence:
    number: int
    text: str
    status: CriterionStatus
    how: How
    source: Source | None = None
    # Why: the reviewer's reason, or why a check did not run.
    note: str = ""


@dataclass(frozen=True)
class CheckRun:
    command: str
    outcome: CheckOutcome
    # Which check it was: "step 2", "final", "criterion 1".
    step: str
    ref: str
    output: str = ""


@dataclass(frozen=True)
class DiffStat:
    files: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class TraceRef:
    conversation_id: int
    run_id: int | None = None


@dataclass(frozen=True)
class Screenshot:
    label: ScreenshotLabel
    ref: str


@dataclass(frozen=True)
class Evidence:
    criteria: tuple[CriterionEvidence, ...] = ()
    checks_run: tuple[CheckRun, ...] = ()
    diff_stat: DiffStat = DiffStat()
    trace_ref: TraceRef | None = None
    screenshots: tuple[Screenshot, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionCheck:
    """YAAP's run of one criterion's command or file check."""

    number: int
    how: Literal["command", "file"]
    command: str
    outcome: CheckOutcome
    output: str = ""


@dataclass(frozen=True)
class Judgement:
    """The reviewer's answer for one criterion without a check."""

    number: int
    verdict: Verdict
    path: str | None = None
    line: int | None = None
    reason: str = ""


# --- Assembly ---------------------------------------------------------------------------------------------------------


def plan_checks(plan: Plan, final: Sequence[FinalCheck]) -> list[CheckRun]:
    """The plan's step checks as the build left them, then its final run of every check on the finished tree."""
    runs: list[CheckRun] = []
    for step in plan.steps:
        outcome = step_outcome(step.status)
        runs.extend(
            CheckRun(check.run, outcome, f"step {step.number}", f"step:{step.number}")
            for check in step.checks
            if check.run
        )
    runs.extend(
        CheckRun(check.command, check.outcome, "final", f"final:{index}", check.output[:MAX_OUTPUT])
        for index, check in enumerate(final)
    )
    return runs


def step_outcome(status: StepStatus) -> CheckOutcome:
    if status == "done":
        return "passed"
    return "failed" if status == "failed" else "not_run"


def assemble(
    criteria: Sequence[Criterion],
    checks: Sequence[CriterionCheck],
    earlier: Sequence[CheckRun] = (),
    trace: TraceRef | None = None,
    notes: Sequence[str] = (),
) -> Evidence:
    """The evidence when the review starts: YAAP's checks per criterion; the criteria without a check wait for the
    reviewer (`not_checked`, how `none`)."""
    runs = list(earlier)
    by_number: dict[int, int] = {}
    for check in checks:
        by_number[check.number] = len(runs)
        runs.append(
            CheckRun(check.command, check.outcome, f"criterion {check.number}", "", check.output[:MAX_OUTPUT])
        )
    runs = [replace(run, ref=f"check:{index}") if not run.ref else run for index, run in enumerate(runs)]
    items = [criterion_evidence(criterion, runs, by_number.get(criterion.number)) for criterion in criteria]
    return Evidence(tuple(items), tuple(runs), DiffStat(), trace, (), tuple(notes))


def criterion_evidence(criterion: Criterion, runs: Sequence[CheckRun], index: int | None) -> CriterionEvidence:
    check = criterion.check
    if not isinstance(check, CommandCheck | FileCheck):
        return CriterionEvidence(criterion.number, criterion.text, "not_checked", "none")
    how: How = "command" if isinstance(check, CommandCheck) else "file"
    if index is None:
        return CriterionEvidence(criterion.number, criterion.text, "not_checked", how, note="The check did not run.")
    run = runs[index]
    source = Source("check_output", run.ref)
    if run.outcome in ("passed", "failed"):
        status: CriterionStatus = "passed" if run.outcome == "passed" else "failed"
        return CriterionEvidence(criterion.number, criterion.text, status, how, source)
    note = run.output.splitlines()[0] if run.output.strip() else "The check did not run."
    return CriterionEvidence(criterion.number, criterion.text, "not_checked", how, source, note[:MAX_REASON])


def awaiting_reviewer(evidence: Evidence) -> list[CriterionEvidence]:
    """The criteria the reviewer judges: those without a check of YAAP's."""
    return [item for item in evidence.criteria if item.how in ("reviewer", "none")]


def with_judgements(evidence: Evidence, judgements: Sequence[Judgement]) -> Evidence:
    """The reviewer's answers on the criteria without a check. `met` or `not_met` counts only with the file it relied
    on; `unclear`, an answer without a file, or no answer leaves the criterion `not_checked`."""
    answers = {judgement.number: judgement for judgement in judgements}
    items = []
    for item in evidence.criteria:
        answer = answers.get(item.number)
        if item.how not in ("reviewer", "none") or answer is None:
            items.append(item)
            continue
        items.append(judged(item, answer))
    return replace(evidence, criteria=tuple(items))


def judged(item: CriterionEvidence, answer: Judgement) -> CriterionEvidence:
    source = None
    if answer.path:
        ref = answer.path if answer.line is None else f"{answer.path}:{answer.line}"
        source = Source("finding", ref)
    reason = answer.reason[:MAX_REASON]
    if answer.verdict == "unclear" or source is None:
        return replace(item, status="not_checked", how="reviewer", source=source, note=reason)
    status: CriterionStatus = "judged_met" if answer.verdict == "met" else "judged_not_met"
    return replace(item, status=status, how="reviewer", source=source, note=reason)


def diff_stat(diff: str) -> DiffStat:
    """Files, added and removed lines of a unified diff."""
    files = insertions = deletions = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++ "):
            insertions += 1
        elif line.startswith("-") and not line.startswith("--- "):
            deletions += 1
    return DiffStat(files, insertions, deletions)


# --- The card's summary -----------------------------------------------------------------------------------------------

STATUS_WORDS: dict[CriterionStatus, str] = {
    "passed": "passed",
    "failed": "failed",
    "judged_met": "met, judged by the reviewer",
    "judged_not_met": "not met, judged by the reviewer",
    "not_checked": "not checked",
}


def evidence_markdown(evidence: Evidence) -> str:
    """The card's `## Evidence`: a checklist of the criteria, the checks that ran and the diff's size."""
    lines = []
    for item in evidence.criteria:
        mark = "x" if item.status in ("passed", "judged_met") else " "
        where = f" ({item.source.ref})" if item.source is not None and item.source.kind == "finding" else ""
        text = " ".join(item.text.split())
        lines.append(f"- [{mark}] {item.number}. {text}: {STATUS_WORDS[item.status]}{where}")
    if not lines:
        lines.append("The card has no acceptance criteria.")
    passed = sum(1 for run in evidence.checks_run if run.outcome == "passed")
    stat = evidence.diff_stat
    lines.append("")
    lines.append(f"Checks that ran: {len(evidence.checks_run)}, {passed} passed.")
    lines.append(f"Diff: {stat.files} files, +{stat.insertions} -{stat.deletions}.")
    lines.extend(f"{note}." if not note.endswith(".") else note for note in evidence.notes)
    return "\n".join(lines)


# --- Stored form ------------------------------------------------------------------------------------------------------


def evidence_to_json(evidence: Evidence) -> dict[str, Any]:
    return {
        "criteria": [
            {
                "number": item.number,
                "text": item.text,
                "status": item.status,
                "how": item.how,
                "source": None if item.source is None else {"kind": item.source.kind, "ref": item.source.ref},
                "note": item.note,
            }
            for item in evidence.criteria
        ],
        "checks_run": [
            {"command": run.command, "outcome": run.outcome, "step": run.step, "ref": run.ref, "output": run.output}
            for run in evidence.checks_run
        ],
        "diff_stat": {
            "files": evidence.diff_stat.files,
            "insertions": evidence.diff_stat.insertions,
            "deletions": evidence.diff_stat.deletions,
        },
        "trace_ref": None
        if evidence.trace_ref is None
        else {"conversation_id": evidence.trace_ref.conversation_id, "run_id": evidence.trace_ref.run_id},
        "screenshots": [{"label": shot.label, "ref": shot.ref} for shot in evidence.screenshots],
        "notes": list(evidence.notes),
    }


def one_of[T: str](value: object, allowed: tuple[T, ...], default: T) -> T:
    return next((item for item in allowed if item == value), default)


def whole(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def items_of(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def evidence_from_json(value: object) -> Evidence | None:
    """The stored evidence; None when there is none. Unknown values fall back to the safest reading."""
    if not isinstance(value, Mapping):
        return None
    criteria = tuple(
        CriterionEvidence(
            whole(item.get("number")),
            str(item.get("text") or ""),
            one_of(item.get("status"), STATUSES, "not_checked"),
            one_of(item.get("how"), HOWS, "none"),
            source_from_json(item.get("source")),
            str(item.get("note") or ""),
        )
        for item in items_of(value.get("criteria"))
    )
    runs = tuple(
        CheckRun(
            str(item.get("command") or ""),
            one_of(item.get("outcome"), OUTCOMES, "not_run"),
            str(item.get("step") or ""),
            str(item.get("ref") or ""),
            str(item.get("output") or ""),
        )
        for item in items_of(value.get("checks_run"))
    )
    stat = value.get("diff_stat")
    stat = stat if isinstance(stat, Mapping) else {}
    trace = value.get("trace_ref")
    trace_ref = None
    if isinstance(trace, Mapping) and whole(trace.get("conversation_id")):
        run_id = trace.get("run_id")
        trace_ref = TraceRef(whole(trace.get("conversation_id")), whole(run_id) or None)
    shots = tuple(
        Screenshot(one_of(item.get("label"), LABELS, "after"), str(item.get("ref") or ""))
        for item in items_of(value.get("screenshots"))
    )
    notes = tuple(str(note) for note in value.get("notes") or [] if isinstance(note, str))
    diff = DiffStat(whole(stat.get("files")), whole(stat.get("insertions")), whole(stat.get("deletions")))
    return Evidence(criteria, runs, diff, trace_ref, shots, notes)


def source_from_json(value: object) -> Source | None:
    if not isinstance(value, Mapping) or not value.get("ref"):
        return None
    kind = next((item for item in SOURCE_KINDS if item == value.get("kind")), None)
    return Source(kind, str(value["ref"])) if kind is not None else None
