"""Learning proposals: made from a conversation, stored, accepted or declined.

`LearningService.review` turns a conversation's signals into `LearningProposal` rows (app/learning/proposals). A
Blueprint proposal is written at once on a branch of its layer (app/learning/branches), where an eval comparison can
read it (app/learning/measuring); a project without eval cases shows it as *not measured* rather than holding it back.
Accepting writes the change onto the layer's current branch as one commit and removes the branch;
declining removes the branch and leaves the layer as it was. A memory line is written on accept, without a
measurement. Nothing is ever applied without the user.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.layers import (
    PROJECT_LAYER_FOLDER,
    BlueprintError,
    Layer,
    LayerKind,
    active_items,
    ensure_repository,
    write_file,
)
from app.blueprint.levels import layers_for
from app.blueprint.rules import unified_diff
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, run_utility
from app.evals.cases import read_cases
from app.git.place import GitPlace, git_place
from app.git.service import GitError
from app.learning.branches import branch_name, is_repository, remove_branch, write_branch
from app.learning.changes import BlueprintKind, blueprint_change
from app.learning.proposals import (
    Draft,
    ReviewInput,
    conversation_signals,
    fallback_drafts,
    parse_drafts,
    review_prompt,
    rule_views,
)
from app.projects.memory import MEMORY_FILE, ProjectMemoryError, add_entry, parse_entries, read_memory
from app.projects.service import project_path
from app.projects.suggestions import accept_entry, conversation_text, similar_entry
from app.storage.models import Conversation, Environment, LearningProposal, Project

logger = logging.getLogger(__name__)

OPEN = "open"
NO_CASES = "The project has no eval cases, so the change was not measured; it can still be accepted."
NO_REPOSITORY = "The layer is not a git repository, so the change cannot wait on a branch to be measured."


class LearningError(Exception):
    """A learning action that cannot be done; the message is safe to show."""


class ProposalNotFound(LearningError):
    """No open proposal with that number in the project."""


@dataclass(frozen=True)
class ProjectScope:
    """A project's folder and Blueprint layers (global, environment, project), read in one session."""

    project_id: int
    environment_id: int
    folder: Path
    layers: list[Layer]

    def level_layers(self, level: LayerKind) -> list[Layer]:
        """The layers a level sees, its own last."""
        index = next((index for index, layer in enumerate(self.layers) if layer.kind == level), None)
        if index is None:
            raise LearningError(f"This project has no {level} Blueprint layer.")
        return self.layers[: index + 1]

    def repository(self, layer: Layer) -> GitPlace:
        """Where git runs for the layer's repository: the environment's runner for a project in the workspace."""
        try:
            return git_place(self.environment_id, layer.repository)
        except GitError as error:
            raise BlueprintError(str(error)) from error


def project_scope(session: Session, blueprint_dir: Path, workspace_dir: Path, project_id: int) -> ProjectScope:
    project = session.get(Project, project_id)
    if project is None:
        raise ProposalNotFound("Project not found.")
    environment = session.get_one(Environment, project.environment_id)
    layers = layers_for(blueprint_dir, workspace_dir, environment, project)
    return ProjectScope(project.id, environment.id, project_path(workspace_dir, project), layers)


def find_open(session: Session, project_id: int, proposal_id: int) -> LearningProposal:
    proposal = session.get(LearningProposal, proposal_id)
    if proposal is None or proposal.project_id != project_id or proposal.status != OPEN:
        raise ProposalNotFound("Proposal not found or already handled.")
    return proposal


def open_proposals(session: Session, project_id: int) -> list[LearningProposal]:
    query = select(LearningProposal).where(LearningProposal.project_id == project_id, LearningProposal.status == OPEN)
    return list(session.scalars(query.order_by(LearningProposal.id)))


def commit_message(text: str, layer: Layer) -> str:
    return f"Learning: {' '.join(text.split())[:60]} ({layer.label})"


@dataclass(frozen=True)
class LearningService:
    utility: UtilityContext
    blueprint_dir: Path
    workspace_dir: Path

    def scope(self, session: Session, project_id: int) -> ProjectScope:
        return project_scope(session, self.blueprint_dir, self.workspace_dir, project_id)

    # --- Review ---------------------------------------------------------------------------------------------------

    def review_input(self, conversation_id: int, since_last: bool) -> ReviewInput | None:
        """What a review reads; None outside a project, or after a goal when nothing new has a signal."""
        with self.utility.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.project_id is None:
                return None
            since = conversation.learning_checked_at if since_last else None
            signals = conversation_signals(session, conversation_id, since)
            if since_last and not signals:
                return None
            scope = self.scope(session, conversation.project_id)
            text = conversation_text(session, conversation_id, since)
            language = resolve_languages(session, scope.environment_id, scope.project_id).work
            pending = [item.text for item in open_proposals(session, scope.project_id)]
        items = active_items(scope.layers)
        skills = [item.name for item in items if item.kind == "skill"]
        memory = read_memory(scope.folder)
        return ReviewInput(
            scope.project_id, scope.environment_id, conversation_id, signals, text, rule_views(items), memory, skills,
            language, pending,
        )

    async def review(self, conversation_id: int, since_last: bool = False) -> list[LearningProposal]:
        """Proposals from the conversation: by the utility model, or the fallback when none answers. After a goal
        (since_last) only what came since the last review is read; on demand the whole conversation."""
        found = await anyio.to_thread.run_sync(self.review_input, conversation_id, since_last)
        if found is None:
            return []
        answer = await run_utility(
            self.utility, found.environment_id, review_prompt(found), conversation_id, max_tokens=1500, task="learning"
        )
        drafts = fallback_drafts(found) if answer is None else parse_drafts(answer, found)
        return await anyio.to_thread.run_sync(self.store, found.project_id, conversation_id, drafts)

    # --- Storing --------------------------------------------------------------------------------------------------

    def store(self, project_id: int, conversation_id: int | None, drafts: list[Draft]) -> list[LearningProposal]:
        """Keep each new draft as an open proposal (a Blueprint one on its branch); returns the new ones."""
        created = []
        with self.utility.session_factory() as session:
            scope = self.scope(session, project_id)
            pending = {(item.kind, item.level, item.text) for item in open_proposals(session, project_id)}
            measurable = any(file.case is not None for file in read_cases(scope.folder))
            for draft in drafts:
                if (draft.kind, draft.level, draft.text) in pending:
                    continue
                try:
                    proposal = self.store_one(session, scope, conversation_id, draft, measurable)
                except (BlueprintError, LearningError) as error:
                    logger.info("A learning proposal was left out: %s", error)
                    continue
                if proposal is not None:
                    created.append(proposal)
                    pending.add((draft.kind, draft.level, draft.text))
            if conversation_id is not None and (conversation := session.get(Conversation, conversation_id)):
                conversation.learning_checked_at = datetime.now(UTC)
            session.commit()
            for proposal in created:
                session.refresh(proposal)
                session.expunge(proposal)
        return created

    def store_one(
        self, session: Session, scope: ProjectScope, conversation_id: int | None, draft: Draft, measurable: bool
    ) -> LearningProposal | None:
        proposal = LearningProposal(
            project_id=scope.project_id,
            conversation_id=conversation_id,
            kind=draft.kind,
            level=draft.level,
            target=draft.target,
            text=draft.text,
            rationale=draft.rationale,
            signals=[signal.as_json() for signal in draft.signals],
            source=draft.source,
            status=OPEN,
            measurement="not_needed",
            created_at=datetime.now(UTC),
        )
        if draft.kind == "memory":
            memory = read_memory(scope.folder)
            if any(entry.text == draft.text for entry in parse_entries(memory)):
                return None
            after = add_entry(memory, draft.target, draft.text)
            proposal.diff = unified_diff(f"{PROJECT_LAYER_FOLDER}/{MEMORY_FILE}", memory, after)
            session.add(proposal)
            return proposal
        layers = scope.level_layers(draft.level)
        change = blueprint_change(cast(BlueprintKind, draft.kind), layers, draft.target, draft.text)
        if change.before == change.after:
            return None
        proposal.target, proposal.diff = change.path, unified_diff(change.path, change.before, change.after)
        session.add(proposal)
        session.flush()
        own = layers[-1]
        if own.kind != "project":
            ensure_repository(own)
        if not is_repository(own):
            proposal.measurement, proposal.measurement_note = "not_measured", NO_REPOSITORY
            return proposal
        try:
            message = commit_message(draft.text, own)
            write_branch(own, scope.repository(own), change.path, change.after, branch_name(proposal.id), message)
        except BlueprintError as error:
            proposal.measurement, proposal.measurement_note = "not_measured", str(error)
            return proposal
        proposal.branch = branch_name(proposal.id)
        proposal.measurement = "ready" if measurable else "not_measured"
        proposal.measurement_note = None if measurable else NO_CASES
        return proposal

    # --- Deciding -------------------------------------------------------------------------------------------------

    def accept(self, project_id: int, proposal_id: int) -> None:
        """Apply the proposal: a Blueprint change as one commit on the layer's current branch (its branch is
        removed), a memory line as one memory commit."""
        with self.utility.session_factory() as session:
            proposal = find_open(session, project_id, proposal_id)
            scope = self.scope(session, project_id)
            try:
                if proposal.kind == "memory":
                    near = similar_entry(parse_entries(read_memory(scope.folder)), proposal.text)
                    accept_entry(
                        scope.folder, scope.environment_id, proposal.target, proposal.text, near.text if near else None
                    )
                else:
                    self.merge(scope, proposal)
            except (BlueprintError, ProjectMemoryError) as error:
                raise LearningError(str(error)) from error
            proposal.status, proposal.decided_at = "accepted", datetime.now(UTC)
            session.commit()

    def merge(self, scope: ProjectScope, proposal: LearningProposal) -> None:
        """The measured change applied to the layer as it is now (one commit), then the branch removed."""
        layers = scope.level_layers(cast(LayerKind, proposal.level))
        own = layers[-1]
        change = blueprint_change(cast(BlueprintKind, proposal.kind), layers, proposal.target, proposal.text)
        if change.before != change.after:
            write_file(own, change.path, change.after, commit_message(proposal.text, own))
        if proposal.branch:
            remove_branch(own, scope.repository(own), proposal.branch)

    def decline(self, project_id: int, proposal_id: int) -> None:
        """Leave the layer as it is and remove the proposal's branch."""
        with self.utility.session_factory() as session:
            proposal = find_open(session, project_id, proposal_id)
            if proposal.branch:
                scope = self.scope(session, project_id)
                try:
                    own = scope.level_layers(cast(LayerKind, proposal.level))[-1]
                    remove_branch(own, scope.repository(own), proposal.branch)
                except BlueprintError as error:
                    raise LearningError(str(error)) from error
            proposal.status, proposal.decided_at = "declined", datetime.now(UTC)
            session.commit()
