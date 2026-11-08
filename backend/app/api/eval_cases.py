"""Eval cases from conversations: propose one, then save the edited case."""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.conversations import ConversationDependency, utility_context
from app.api.dependencies import ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.blueprint.levels import project_layer
from app.checkpoints.pins import case_owner, pin_checkpoint
from app.checkpoints.service import CheckpointStore
from app.core.utility import run_utility
from app.evals.case_writer import CaseExistsError, CaseWriteError, save_case
from app.evals.cases import DEFAULT_COMMAND_TIMEOUT
from app.evals.conversation_facts import CaseFromConversationError
from app.evals.from_conversation import CaseProposal, ChecksFrom, propose_for_conversation
from app.evals.models import Budget, Check, CommandCheck, EvalCase, FileCheck, RubricCheck, StartState
from app.evals.project_commands import TestCommandSource
from app.projects.service import ProjectError, project_path
from app.storage.models import Checkpoint, Project

router = APIRouter(tags=["evals"])

PROPOSAL_MAX_TOKENS = 1200


class StartIO(BaseModel):
    """Exactly one of the two: a commit of the project, or a checkpoint's number."""

    commit: str | None = None
    checkpoint: int | None = None


class BudgetIO(BaseModel):
    steps: int | None = None
    tokens: int | None = None
    cost_usd: float | None = None
    seconds: float | None = None


class CommandCheckIO(BaseModel):
    kind: Literal["command"] = "command"
    # Empty: the name the case file's reader gives the check.
    name: str = ""
    run: str
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT


class FileCheckIO(BaseModel):
    kind: Literal["file"] = "file"
    name: str = ""
    path: str
    exists: bool = True
    contains: list[str] = []
    not_contains: list[str] = []
    matches: list[str] = []
    not_matches: list[str] = []


class RubricCheckIO(BaseModel):
    kind: Literal["rubric"] = "rubric"
    name: str = ""
    rubric: str
    files: list[str] = []


CheckIO = Annotated[CommandCheckIO | FileCheckIO | RubricCheckIO, Field(discriminator="kind")]


class EvalCaseIO(BaseModel):
    name: str = Field(max_length=60)
    summary: str = ""
    prompt: str = Field(max_length=100_000)
    start: StartIO
    agent: str | None = None
    preset: str | None = None
    budget: BudgetIO | None = None
    checks: list[CheckIO] = Field(default=[], max_length=50)


class EvalCaseProposalOut(BaseModel):
    case: EvalCaseIO
    # Where the proposed start comes from, and both candidates, so the dialog can offer the other one.
    start_source: Literal["commit", "checkpoint", "none"]
    commit_before: str | None
    first_checkpoint: int | None
    checks_from: ChecksFrom
    test_command: str | None
    test_command_source: TestCommandSource | None
    changed_files: list[str]
    notes: list[str]


class EvalCaseSavedOut(BaseModel):
    case: EvalCaseIO
    path: str
    commit: str


def check_out(check: Check) -> CheckIO:
    if isinstance(check, CommandCheck):
        return CommandCheckIO(name=check.name, run=check.run, timeout_seconds=check.timeout_seconds)
    if isinstance(check, FileCheck):
        return FileCheckIO(
            name=check.name,
            path=check.path,
            exists=check.exists,
            contains=list(check.contains),
            not_contains=list(check.not_contains),
            matches=list(check.matches),
            not_matches=list(check.not_matches),
        )
    return RubricCheckIO(name=check.name, rubric=check.rubric, files=list(check.files))


def check_in(check: CheckIO) -> Check:
    if isinstance(check, CommandCheckIO):
        return CommandCheck(check.name, check.run.strip(), check.timeout_seconds)
    if isinstance(check, FileCheckIO):
        return FileCheck(
            check.name,
            check.path.strip(),
            check.exists,
            tuple(check.contains),
            tuple(check.not_contains),
            tuple(check.matches),
            tuple(check.not_matches),
        )
    return RubricCheck(check.name, check.rubric.strip(), tuple(path.strip() for path in check.files))


def case_out(case: EvalCase) -> EvalCaseIO:
    budget = case.budget
    return EvalCaseIO(
        name=case.name,
        summary=case.summary,
        prompt=case.prompt,
        start=StartIO(commit=case.start.commit, checkpoint=case.start.checkpoint),
        agent=case.agent,
        preset=case.preset,
        budget=BudgetIO(steps=budget.steps, tokens=budget.tokens, cost_usd=budget.cost_usd, seconds=budget.seconds)
        if budget is not None
        else None,
        checks=[check_out(check) for check in case.checks],
    )


def case_in(payload: EvalCaseIO) -> EvalCase:
    budget = payload.budget
    parts = (budget.steps, budget.tokens, budget.cost_usd, budget.seconds) if budget is not None else ()
    commit = (payload.start.commit or "").strip() or None
    if commit is not None and payload.start.checkpoint is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Start at a commit or at a checkpoint, not both.")
    return EvalCase(
        name=payload.name.strip(),
        prompt=payload.prompt.strip(),
        start=StartState(commit=commit, checkpoint=payload.start.checkpoint),
        checks=tuple(check_in(check) for check in payload.checks),
        summary=" ".join(payload.summary.split()),
        agent=(payload.agent or "").strip() or None,
        preset=(payload.preset or "").strip() or None,
        budget=Budget(*parts) if any(part is not None for part in parts) else None,
    )


def proposal_out(proposal: CaseProposal) -> EvalCaseProposalOut:
    facts, test = proposal.facts, proposal.test_command
    return EvalCaseProposalOut(
        case=case_out(proposal.case),
        start_source=facts.start_source,
        commit_before=facts.commit_before,
        first_checkpoint=facts.first_checkpoint,
        checks_from=proposal.checks_from,
        test_command=test.command if test else None,
        test_command_source=test.source if test else None,
        changed_files=list(facts.changed_files),
        notes=list(proposal.notes),
    )


def folder_of(workspace_dir: Path, project: Project) -> Path:
    try:
        return project_path(workspace_dir, project)
    except ProjectError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post("/conversations/{conversation_id}/eval-case-proposal", response_model=EvalCaseProposalOut)
async def propose_eval_case(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
) -> EvalCaseProposalOut:
    """A case to edit from this conversation: its start, its first message as the prompt, and proposed checks."""
    project = session.get(Project, conversation.project_id) if conversation.project_id is not None else None
    if project is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a conversation in a project can become an eval case.")
    folder = folder_of(workspace_dir, project)
    state = request.app.state
    checkpoints = CheckpointStore(state.session_factory, state.data_dir)
    context = utility_context(request)

    async def ask(prompt: str) -> str | None:
        return await run_utility(
            context,
            conversation.environment_id,
            prompt,
            conversation.id,
            max_tokens=PROPOSAL_MAX_TOKENS,
            task="eval_case",
        )

    try:
        proposal = await propose_for_conversation(session, checkpoints, conversation, folder, ask)
    except CaseFromConversationError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return proposal_out(proposal)


@router.post("/projects/{project_id}/eval-cases", response_model=EvalCaseSavedOut, status_code=status.HTTP_201_CREATED)
def save_eval_case(
    payload: EvalCaseIO, project: ProjectDependency, workspace_dir: WorkspaceDirDependency, session: SessionDependency
) -> EvalCaseSavedOut:
    """Write the case to the project's .blueprint/evals/<name>.md as one commit; an existing name is refused. A case
    that starts at a checkpoint pins it, so the checkpoint pruning keeps it."""
    folder_of(workspace_dir, project)
    case = case_in(payload)
    checkpoint = session.get(Checkpoint, case.start.checkpoint) if case.start.checkpoint is not None else None
    if case.start.checkpoint is not None and (checkpoint is None or checkpoint.project_id != project.id):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"The project has no checkpoint {case.start.checkpoint}."
        )
    try:
        saved = save_case(project_layer(workspace_dir, project), case)
    except CaseExistsError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except CaseWriteError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if checkpoint is not None:
        pin_checkpoint(session, checkpoint.id, case_owner(project.id, saved.case.name))
        session.commit()
    return EvalCaseSavedOut(case=case_out(saved.case), path=saved.path, commit=saved.commit)
