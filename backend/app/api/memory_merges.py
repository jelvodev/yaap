"""Memory consolidation in the Memory tab: tidy up, and accept or reject each proposed merge."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.api.projects import memory_entries, memory_error
from app.api.schemas import MemoryEntriesOut
from app.core.utility_skips import project_skip_message
from app.projects.consolidation import accept_merge, app_context, open_merges, refresh_open, tidy
from app.projects.memory import MAX_ENTRY_CHARS, MemoryChangedError, ProjectMemoryError, read_memory
from app.projects.service import project_path
from app.storage.models import MemoryMerge, Project

router = APIRouter(tags=["projects"])


class MergedEntryOut(BaseModel):
    section: str
    text: str


class MemoryMergeOut(BaseModel):
    id: int
    # The entries the merge replaces, in the order of the memory file.
    entries: list[MergedEntryOut]
    # The merged entry; it takes the place of the first entry it replaces, in that entry's section.
    section: str
    text: str
    created_at: datetime


class AcceptMergeIn(BaseModel):
    # The merged text as the user edited it; empty keeps the proposal's.
    text: str = Field(default="", max_length=MAX_ENTRY_CHARS)


def merge_out(merge: MemoryMerge) -> MemoryMergeOut:
    return MemoryMergeOut(
        id=merge.id,
        entries=[MergedEntryOut(section=item.get("section", ""), text=item.get("text", "")) for item in merge.entries],
        section=merge.section,
        text=merge.text,
        created_at=merge.created_at,
    )


def find_merge(session: Session, project: Project, merge_id: int) -> MemoryMerge:
    merge = session.get(MemoryMerge, merge_id)
    if merge is None or merge.project_id != project.id or merge.status != "open":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Merge not found or already handled.")
    return merge


@router.get("/projects/{project_id}/memory/merges", response_model=list[MemoryMergeOut])
def list_memory_merges(
    project: ProjectDependency, session: SessionDependency, workspace_dir: WorkspaceDirDependency
) -> list[MemoryMergeOut]:
    """Merges waiting for the user; those an edit of the memory overtook are dropped as outdated."""
    memory = read_memory(project_path(workspace_dir, project))
    return [merge_out(merge) for merge in refresh_open(session, project.id, memory)]


@router.post("/projects/{project_id}/memory/tidy", response_model=list[MemoryMergeOut])
async def tidy_memory(
    project: ProjectDependency, session: SessionDependency, request: Request, workspace_dir: WorkspaceDirDependency
) -> list[MemoryMergeOut]:
    """Ask the utility model now for merges of duplicate or overlapping entries; returns all open merges."""
    project_id, folder = project.id, project_path(workspace_dir, project)
    session.close()
    created = await tidy(app_context(request.app), project_id, folder)
    if created is None:
        with request.app.state.session_factory() as fresh:
            skipped = project_skip_message(fresh, project_id, "memory_tidy")
        if skipped is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, skipped)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The utility model is not available; check the connection in Settings."
        )
    with request.app.state.session_factory() as fresh:
        return [merge_out(merge) for merge in open_merges(fresh, project_id)]


@router.post("/projects/{project_id}/memory/merges/{merge_id}/accept", response_model=MemoryEntriesOut)
def accept_memory_merge(
    merge_id: int,
    payload: AcceptMergeIn,
    project: ProjectDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
) -> MemoryEntriesOut:
    """Replace the merged entries by the merged text: one commit of the memory file."""
    merge = find_merge(session, project, merge_id)
    folder = project_path(workspace_dir, project)
    try:
        accept_merge(folder, project.environment_id, merge, " ".join(payload.text.split()) or None)
    except MemoryChangedError as error:
        merge.status, merge.decided_at = "outdated", datetime.now(UTC)
        session.commit()
        raise memory_error(error) from error
    except ProjectMemoryError as error:
        raise memory_error(error) from error
    merge.status, merge.decided_at = "accepted", datetime.now(UTC)
    session.commit()
    return memory_entries(read_memory(folder))


@router.delete("/projects/{project_id}/memory/merges/{merge_id}", status_code=status.HTTP_204_NO_CONTENT)
def reject_memory_merge(merge_id: int, project: ProjectDependency, session: SessionDependency) -> Response:
    """Keep the entries as they are."""
    merge = find_merge(session, project, merge_id)
    merge.status, merge.decided_at = "rejected", datetime.now(UTC)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
