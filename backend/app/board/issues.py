"""GitHub issues into the board's Inbox, one way: by number or by label, each as a card with origin `issue` and the
issue's URL. An issue imported before (a card with the same origin ref) is skipped; nothing is written to GitHub.

An issue's title and body are written by anyone who can open an issue, so they are data: the body goes into the
card's description with card headings moved in, and the board shows outside text plainly. It never reaches a model
except through the card's fenced data block.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy import select

from app.board.card import Kind, Origin
from app.board.errors import GitHubFailed, NoGitHubRepository
from app.board.intake import CardIntake
from app.board.models import BoardCardRow
from app.board.operations import NewCard
from app.board.stage_text import escape_headings
from app.git.common import GitError
from app.git.place import git_place
from app.git.service import remote_url
from app.github.client import GitHubClient, GitHubError, Issue, repository_name
from app.projects.service import ProjectError, project_path
from app.storage.models import Project

MAX_IMPORT = 50
MAX_BODY = 20_000
MAX_TITLE = 200
SkipReason = Literal["imported", "pull_request", "not_found", "limit"]


class IssueSource(Protocol):
    def issues(
        self, full_name: str, state: str = "open", labels: Sequence[str] = (), since: str | None = None, limit: int = 100
    ) -> list[Issue]: ...

    def issue(self, full_name: str, number: int) -> Issue | None: ...


# Opens a GitHub client with the token; tests pass a stub.
OpenSource = Callable[[str | None], AbstractContextManager[IssueSource]]


@dataclass(frozen=True)
class IssueSelection:
    """Which issues to import: these numbers, or the open issues with this label."""

    numbers: tuple[int, ...] = ()
    label: str | None = None


@dataclass(frozen=True)
class ImportedIssue:
    number: int
    card_id: int
    title: str


@dataclass(frozen=True)
class SkippedIssue:
    number: int
    reason: SkipReason
    card_id: int | None = None


@dataclass(frozen=True)
class ImportResult:
    repository: str
    added: tuple[ImportedIssue, ...]
    skipped: tuple[SkippedIssue, ...]


# --- Pure ----------------------------------------------------------------------------------------------------------

KIND_WORDS: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    ("bug", ("bug", "defect", "regression")),
    ("refactor", ("refactor",)),
    ("chore", ("chore", "dependencies", "maintenance", "ci")),
)


def issue_kind(labels: Sequence[str]) -> Kind:
    """The card's kind from the issue's labels; a feature when none says otherwise."""
    names = [label.lower() for label in labels]
    for kind, words in KIND_WORDS:
        if any(word in name for name in names for word in words):
            return kind
    return "feature"


def issue_title(issue: Issue) -> str:
    title = " ".join(issue.title.split()) or f"Issue #{issue.number}"
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"


def issue_card(issue: Issue, full_name: str) -> NewCard:
    """The card for an issue: in Inbox, origin `issue` with its URL, the body as the description."""
    body = issue.body.strip()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rstrip() + "\n\n[The issue is longer; the rest is on GitHub.]"
    labels = f" Labels: {', '.join(issue.labels)}." if issue.labels else ""
    header = f"Imported from GitHub issue {full_name}#{issue.number}.{labels}"
    description = "\n\n".join(part for part in (header, escape_headings(body)) if part)
    return NewCard(
        title=issue_title(issue),
        kind=issue_kind(issue.labels),
        column="inbox",
        description=description,
        origin=Origin(type="issue", ref=issue.html_url or f"https://github.com/{full_name}/issues/{issue.number}"),
    )


# --- The import ----------------------------------------------------------------------------------------------------


def open_github(token: str | None) -> AbstractContextManager[IssueSource]:
    return GitHubClient(token)


def project_repository(project: Project, workspace_dir: Path | None) -> str | None:
    """owner/name of the project's GitHub repository: its URL, else the origin of its repository."""
    name = repository_name(project.repository_url)
    if name is not None or workspace_dir is None:
        return name
    try:
        return repository_name(remote_url(git_place(project.environment_id, project_path(workspace_dir, project))))
    except (GitError, ProjectError):
        return None


@dataclass(frozen=True)
class IssueImporter:
    intake: CardIntake
    open_source: OpenSource = open_github

    def import_issues(
        self, project_id: int, selection: IssueSelection, github_token: Callable[[Project], str | None]
    ) -> ImportResult:
        """Import the selected issues into Inbox; already imported ones and pull requests are skipped, and so is
        what would take Inbox over its limit. The token is read once the board and its repository are known. Raises
        a BoardError (board off, no GitHub repository, GitHub failed)."""
        board = self.intake.board
        opened = board.opened(project_id)
        full_name = project_repository(opened.project, board.workspace_dir)
        if full_name is None:
            raise NoGitHubRepository()
        found, skipped = self.fetch(full_name, selection, github_token(opened.project))
        with board.session_factory() as session:
            query = select(BoardCardRow.origin_ref, BoardCardRow.card_id).where(
                BoardCardRow.project_id == project_id, BoardCardRow.origin_type == "issue"
            )
            imported = {ref: card_id for ref, card_id in session.execute(query) if ref is not None}
            room = max(0, self.intake.room_in_inbox(session, project_id))
        added: list[ImportedIssue] = []
        for issue in found:
            card = issue_card(issue, full_name)
            ref = card.origin.ref or ""
            if ref in imported:
                skipped.append(SkippedIssue(issue.number, "imported", imported[ref]))
            elif len(added) >= room:
                skipped.append(SkippedIssue(issue.number, "limit"))
            else:
                detail = board.create_card(project_id, card)
                imported[ref] = detail.view.id
                added.append(ImportedIssue(issue.number, detail.view.id, card.title))
        return ImportResult(full_name, tuple(added), tuple(skipped))

    def fetch(
        self, full_name: str, selection: IssueSelection, token: str | None
    ) -> tuple[list[Issue], list[SkippedIssue]]:
        found: list[Issue] = []
        skipped: list[SkippedIssue] = []
        try:
            with self.open_source(token) as source:
                if selection.label is not None:
                    return source.issues(full_name, "open", [selection.label], limit=MAX_IMPORT), skipped
                for number in dict.fromkeys(selection.numbers):
                    try:
                        issue = source.issue(full_name, number)
                    except GitHubError as error:
                        if error.status not in (404, 410):
                            raise
                        skipped.append(SkippedIssue(number, "not_found"))
                        continue
                    if issue is None:
                        skipped.append(SkippedIssue(number, "pull_request"))
                    else:
                        found.append(issue)
        except GitHubError as error:
            raise GitHubFailed(f"GitHub: {error}") from error
        return found, skipped
