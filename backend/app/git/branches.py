"""Local branches of a repository."""

from dataclasses import dataclass

from app.git.common import GitError, UnsafeRepositoryError
from app.git.service import PLAIN_DIFF, Repository, repository_status, run_git


@dataclass
class Branch:
    name: str
    current: bool
    upstream: str | None
    # "ahead 2, behind 1" as git reports it; empty when even or without upstream.
    track: str
    subject: str


def check_branch_name(repository: Repository, name: str) -> str:
    name = name.strip()
    if not name or name.startswith("-"):
        raise GitError("Name the branch, such as feature/login.")
    try:
        run_git(["check-ref-format", "--branch", name], repository)
    except UnsafeRepositoryError:
        raise
    except GitError as error:
        raise GitError(f"{name} is not a valid branch name.") from error
    return name


def list_branches(repository: Repository) -> list[Branch]:
    output = run_git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(HEAD)%00%(refname:short)%00%(upstream:short)%00%(upstream:track,nobracket)%00"
            "%(contents:subject)",
            "refs/heads",
        ],
        repository,
    )
    return parse_branches(output.stdout)


def parse_branches(output: str) -> list[Branch]:
    """Parse the null-delimited output of `for-each-ref`."""
    branches = []
    for line in output.splitlines():
        head, name, upstream, track, subject = (line.split("\0") + [""] * 5)[:5]
        branches.append(Branch(name, head == "*", upstream or None, track, subject))
    return branches


def create_branch(repository: Repository, name: str, start: str | None = None, switch: bool = True) -> None:
    name = check_branch_name(repository, name)
    arguments = ["switch", "-c", name] if switch else ["branch", name]
    if start:
        arguments.append(start)
    run_git(arguments, repository)


def switch_branch(repository: Repository, name: str) -> None:
    """Switch to a local branch, or to a new local branch tracking origin/<name>. Changes that would be lost stop it."""
    run_git(["switch", check_branch_name(repository, name)], repository)


def delete_branch(repository: Repository, name: str, force: bool = False) -> None:
    name = check_branch_name(repository, name)
    status = repository_status(repository)
    if status.branch == name:
        raise GitError("This is the current branch. Switch to another branch first.")
    try:
        run_git(["branch", "-D" if force else "-d", name], repository)
    except GitError as error:
        if "not fully merged" in str(error):
            raise GitError(f"{name} has commits that are not merged. Delete it anyway to lose them.") from error
        raise


def branch_summary(repository: Repository, base: str, limit: int = 60) -> tuple[str, str]:
    """The commits on HEAD since base, and the changed files with their size of change, for a pull request text."""
    for candidate in (f"origin/{base}", base):
        try:
            log = run_git(["log", "--format=- %s%n%b", f"-{limit}", f"{candidate}..HEAD"], repository).stdout
            stat = run_git(["diff", "--stat", *PLAIN_DIFF, f"{candidate}...HEAD"], repository).stdout
            return log.strip(), stat.strip()
        except UnsafeRepositoryError:
            raise
        except GitError:
            continue
    raise GitError(f"The base branch {base} is not known here. Fetch first.")
