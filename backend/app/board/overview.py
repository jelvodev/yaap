"""Building the board's answer: the cards and versions as the index holds them, next to the version files read from
the branch."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.board.models import BoardCardRow, BoardVersionRow, StorageKind
from app.board.repository import BoardRepository
from app.board.versions import BrokenVersion, Version, parse_version
from app.board.views import BoardView, PushView, Related, card_view, columns_of, current_version, version_views
from app.core.settings import Source


def version_files(repository: BoardRepository, head: str | None) -> dict[str, Version | BrokenVersion]:
    if head is None:
        return {}
    files = [entry.path for entry in repository.list_files(head) if entry.path.startswith("versions/")]
    found: dict[str, Version | BrokenVersion] = {}
    for path, content in repository.read_many(files, head).items():
        name = path.removeprefix("versions/").removesuffix(".md")
        found[name] = (
            parse_version(content.text, path)
            if content.text is not None
            else BrokenVersion(path, name, "The file could not be read.")
        )
    return found


def board_view(
    session_factory: sessionmaker[Session],
    project_id: int,
    source: Source,
    storage: StorageKind,
    location: str,
    head: str | None,
    versions: Mapping[str, Version | BrokenVersion],
    push_allowed: bool,
    last_pushed: str | None,
) -> BoardView:
    with session_factory() as session:
        # Without a head the branch is gone; its rows are kept for when it comes back, but not shown.
        query = select(BoardCardRow).where(BoardCardRow.project_id == project_id)
        rows = list(session.scalars(query)) if head is not None else []
        version_query = select(BoardVersionRow).where(BoardVersionRow.project_id == project_id)
        version_rows = list(session.scalars(version_query))
        related = Related.read(session, project_id, rows)
    views = sorted((card_view(row, related) for row in rows), key=lambda view: view.id)
    version_list = version_views(version_rows, versions, [view for view in views if not view.broken])
    return BoardView(
        enabled=True,
        enabled_source=source,
        state="ready",
        storage=storage,
        location=location,
        head=head,
        columns=columns_of(view for view in views if not view.broken),
        broken=tuple(view for view in views if view.broken),
        versions=version_list,
        current_version=current_version(version_list),
        push=PushView(push_allowed, head is not None and head == last_pushed),
    )
