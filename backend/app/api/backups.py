import shutil
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sqlalchemy import select

from app.api.dependencies import CipherDependency, DataDirDependency, SessionDependency
from app.api.schemas import BackupOut
from app.backups.destinations import DestinationError, fetch_from
from app.backups.runs import BackupBusy, backup_running, make_destination, next_backup_at, read_destinations, run_backup
from app.core.backup import BackupError, backup_path, backups_dir, info, list_backups
from app.storage.models import BackupRun
from app.core.restore import RestoreError, check_backup, read_manifest, stage_restore
from app.storage.db import known_revision

router = APIRouter(tags=["backups"])


@router.get("/backups", response_model=list[BackupOut])
def read_backups(data_dir: DataDirDependency) -> list[BackupOut]:
    return [BackupOut(**asdict(item)) for item in list_backups(data_dir)]


class BackupRunOut(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    name: str | None
    size: int | None
    results: list[dict[str, Any]]
    error: str | None


class DestinationStateOut(BaseModel):
    name: str
    kind: str
    enabled: bool
    keep: int
    where: str
    ok: bool
    message: str
    free_space: int | None
    backups: list[BackupOut]


class BackupStatusOut(BaseModel):
    running: bool
    next_at: datetime | None
    # The newest scheduled backup, when it failed or missed a destination; shown in the header.
    failed: BackupRunOut | None


def run_out(run: BackupRun) -> BackupRunOut:
    return BackupRunOut(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        name=run.name,
        size=run.size,
        results=list(run.results or []),
        error=run.error,
    )


@router.post("/backups", response_model=BackupOut, status_code=status.HTTP_201_CREATED)
def make_backup(request: Request) -> BackupOut:
    """Make a backup now and copy it to the destinations; the run is recorded like a scheduled one."""
    try:
        run = run_backup(request.app.state, "manual")
    except BackupBusy as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if run.status == "failed" or run.name is None:
        raise HTTPException(status.HTTP_409_CONFLICT, run.error or "The backup failed.")
    return BackupOut(**asdict(info(backup_path(request.app.state.data_dir, run.name))))


@router.get("/backups/runs", response_model=list[BackupRunOut])
def backup_runs(session: SessionDependency) -> list[BackupRunOut]:
    runs = session.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(30))
    return [run_out(run) for run in runs]


@router.get("/backups/status", response_model=BackupStatusOut)
def backup_status(request: Request, session: SessionDependency) -> BackupStatusOut:
    since = getattr(request.app.state, "backup_since", None) or datetime.now(UTC)
    latest = session.scalar(
        select(BackupRun)
        .where(BackupRun.trigger.in_(("schedule", "missed")), BackupRun.status != "running")
        .order_by(BackupRun.started_at.desc())
        .limit(1)
    )
    return BackupStatusOut(
        running=backup_running(),
        next_at=next_backup_at(session, since),
        failed=run_out(latest) if latest is not None and latest.status in ("failed", "partial") else None,
    )


def destination_state(config, session, cipher) -> DestinationStateOut:  # type: ignore[no-untyped-def]
    try:
        destination = make_destination(config, session, cipher)
        where = destination.describe()
        found = destination.list_backups()
        free = destination.free_space()
        return DestinationStateOut(
            name=config.name, kind=config.kind, enabled=config.enabled, keep=config.keep, where=where, ok=True,
            message=f"{len(found)} backup(s) there.", free_space=free,
            backups=[BackupOut(name=item.name, size=item.size, created_at=info_time(item.name)) for item in found],
        )
    except DestinationError as error:
        return DestinationStateOut(
            name=config.name, kind=config.kind, enabled=config.enabled, keep=config.keep,
            where=config.path or f"//{config.server}/{config.share}", ok=False, message=str(error),
            free_space=None, backups=[],
        )


def info_time(name: str) -> datetime:
    return datetime.strptime(name[len("yaap-backup-") : -len(".zip")], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def find_destination(session, name: str):  # type: ignore[no-untyped-def]
    for config in read_destinations(session):
        if config.name == name:
            return config
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No destination with that name.")


@router.get("/backups/destinations", response_model=list[DestinationStateOut])
def destinations(session: SessionDependency, cipher: CipherDependency) -> list[DestinationStateOut]:
    """Every destination with whether it can be reached, its free space and the backups there."""
    return [destination_state(config, session, cipher) for config in read_destinations(session)]


@router.post("/backups/destinations/{name}/test", response_model=DestinationStateOut)
def test_destination(name: str, session: SessionDependency, cipher: CipherDependency) -> DestinationStateOut:
    """Write and remove a test file, and show the free space."""
    config = find_destination(session, name)
    try:
        destination = make_destination(config, session, cipher)
        probe = ".yaap-test.partial"
        with destination.open_write(probe) as writer:
            writer.write(b"YAAP backup destination test")
        destination.remove(probe)
    except DestinationError as error:
        return destination_state(config, session, cipher).model_copy(update={"ok": False, "message": str(error)})
    except Exception as error:  # noqa: BLE001
        return destination_state(config, session, cipher).model_copy(update={"ok": False, "message": f"The test failed: {error}"})
    state = destination_state(config, session, cipher)
    return state.model_copy(update={"message": f"Writing works. {state.message}"}) if state.ok else state


@router.post("/backups/destinations/{name}/fetch/{backup}", response_model=BackupOut)
def fetch_backup(name: str, backup: str, session: SessionDependency, cipher: CipherDependency, data_dir: DataDirDependency) -> BackupOut:
    """Copy a backup from a destination into YAAP's own backups; then it can be restored as usual."""
    config = find_destination(session, name)
    try:
        path = fetch_from(make_destination(config, session, cipher), backup, backups_dir(data_dir))
    except DestinationError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return BackupOut(**asdict(info(path)))


@router.post("/backups/upload", response_model=BackupOut, status_code=status.HTTP_201_CREATED)
def upload_backup(file: UploadFile, data_dir: DataDirDependency) -> BackupOut:
    """Add a backup made elsewhere (or downloaded earlier), named after the time it was made."""
    folder = backups_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".upload-{datetime.now(UTC):%Y%m%d%H%M%S%f}.partial"
    try:
        with partial.open("wb") as target:
            shutil.copyfileobj(file.file, target, length=1024 * 1024)
        try:
            with zipfile.ZipFile(partial) as archive:
                manifest = read_manifest(archive)
        except zipfile.BadZipFile as error:
            raise RestoreError("The file is not a readable zip.") from error
        created = datetime.fromisoformat(str(manifest.get("created_at")))
        name = f"yaap-backup-{created.astimezone(UTC):%Y%m%d-%H%M%S}.zip"
        target_path = folder / name
        if target_path.exists():
            raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already here.")
        partial.replace(target_path)
    except RestoreError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The backup's manifest has no valid date.") from error
    finally:
        partial.unlink(missing_ok=True)
    return BackupOut(**asdict(info(target_path)))


class RestoreCheckOut(BaseModel):
    name: str
    created_at: datetime | None
    database_revision: str | None
    # Number of files per folder in the backup.
    files: dict[str, int]
    # Every reason the backup cannot be restored; empty when it can.
    problems: list[str]
    restorable: bool


class RestoreIn(BaseModel):
    # The backup's name, typed by the user, to confirm.
    confirm: str


class RestoreOut(BaseModel):
    # The backup made of the current state just before.
    safety_backup: str
    restarting: bool


def find_backup(data_dir: Any, name: str):  # type: ignore[no-untyped-def]
    try:
        return backup_path(data_dir, name)
    except BackupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/backups/{name}/restore-check", response_model=RestoreCheckOut)
def check_restore(name: str, request: Request, data_dir: DataDirDependency) -> RestoreCheckOut:
    """Whether the backup can be restored here, and what it holds."""
    check = check_backup(find_backup(data_dir, name), request.app.state.cipher, known_revision)
    return RestoreCheckOut(name=name, restorable=check.restorable, **asdict(check))


@router.post("/backups/{name}/restore", response_model=RestoreOut)
def restore_backup(name: str, payload: RestoreIn, request: Request, data_dir: DataDirDependency) -> RestoreOut:
    """Replace YAAP's data with the backup: a fresh backup first, then YAAP restarts and puts the backup in place."""
    path = find_backup(data_dir, name)
    if payload.confirm.strip() != name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Type the backup's name to confirm the restore.")
    try:
        safety = stage_restore(data_dir, path, request.app.state.cipher, known_revision)
    except RestoreError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    request.app.state.request_restart()
    return RestoreOut(safety_backup=safety.name, restarting=True)


@router.get("/backups/{name}", response_class=FileResponse)
def download_backup(name: str, data_dir: DataDirDependency) -> FileResponse:
    path = find_backup(data_dir, name)
    return FileResponse(path, media_type="application/zip", filename=name)

