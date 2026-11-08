"""Making the repository map of a project folder, or taking it from the store when the folder's revision is known.

Bounded in time: listing the files and the revision get MAP_SECONDS together; outlining stops at the same deadline
and the map is made from what was outlined by then. At most MAX_OUTLINED files are read, the most central first by
their path, each up to MAX_FILE_BYTES.
"""

import asyncio
import logging
from dataclasses import dataclass

import anyio

from app.evals.footprint import CHARS_PER_TOKEN
from app.outline.symbols import Outliner, OutlineError, Symbol
from app.repomap.listing import project_files, revision
from app.repomap.ranking import (
    Module,
    is_entry_point,
    is_source,
    is_test,
    module_score,
    package_folders,
    path_score,
    public_symbols,
    ranked,
    spread,
)
from app.repomap.render import render_map
from app.repomap.store import RepoMapStore
from app.targets.base import CommandExecutor, TargetError, TargetFiles

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_TOKENS = 1_500
MAP_SECONDS = 10.0
MAX_OUTLINED = 150
MAX_FILE_BYTES = 256_000
READ_BATCH = 8
# Part of every cache key, so a change to how maps are made does not serve maps made the old way.
MAP_VERSION = 1


@dataclass(frozen=True)
class MapSource:
    """Where the map reads the project: the run's files and executor, and the outliner of read_file."""

    files: TargetFiles
    executor: CommandExecutor
    outliner: Outliner
    # The target and folder the files are in, so the maps of two folders with changes are kept apart.
    location: str


@dataclass(frozen=True)
class RepoMap:
    text: str
    key: str
    cached: bool


async def build_repo_map(
    source: MapSource,
    store: RepoMapStore,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    seconds: float = MAP_SECONDS,
) -> RepoMap:
    """The folder's map: from the store for a known revision, else made now and stored. Raises TimeoutError when
    the files cannot be listed in time, and TargetError or OSError when they cannot be read at all."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    async with asyncio.timeout(seconds):
        found = await revision(source.files, source.executor)
        key = f"v{MAP_VERSION}:{budget_tokens}:{found.key}" + ("" if found.shared else f":{source.location}")
        stored = await anyio.to_thread.run_sync(store.get, key)
        if stored is not None:
            return RepoMap(stored, key, cached=True)
        files = await project_files(source.executor, found)
    modules = await outline_modules(source, files, deadline)
    text = render_map(found.label, files, modules, budget_tokens, budget_tokens * CHARS_PER_TOKEN)
    await anyio.to_thread.run_sync(store.put, key, text)
    return RepoMap(text, key, cached=False)


async def outline_modules(source: MapSource, files: tuple[str, ...], deadline: float) -> list[Module]:
    """The source files with their public symbols and scores, most central first, as far as the deadline allows."""
    packages = package_folders(files)
    sources = [path for path in files if is_source(path)]
    candidates = spread(sources, lambda path: path_score(path, packages))[:MAX_OUTLINED]
    loop = asyncio.get_running_loop()
    outlined: dict[str, tuple[Symbol, ...]] = {}
    for start in range(0, len(candidates), READ_BATCH):
        batch = candidates[start : start + READ_BATCH]
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            found = await asyncio.wait_for(asyncio.gather(*(symbols_of(source, path) for path in batch)), remaining)
        except TimeoutError:
            logger.info("The repository map stopped outlining at its time limit after %d files.", len(outlined))
            break
        outlined.update(zip(batch, found))
    modules = [
        Module(path, symbols, module_score(path, packages, symbols))
        for path, symbols in outlined.items()
        if symbols or is_entry_point(path)
    ]
    return ranked(modules)


async def symbols_of(source: MapSource, path: str) -> tuple[Symbol, ...]:
    """A file's public symbols; none when it is too large, unreadable or has no outline."""
    try:
        entry = await source.files.stat(path)
        if entry is None or entry.is_dir or entry.size > MAX_FILE_BYTES:
            return ()
        text = (await source.files.read_bytes(path)).decode("utf-8", errors="replace")
        return public_symbols(await source.outliner.outline(path, text), with_methods=not is_test(path))
    except (TargetError, OSError, OutlineError, RecursionError, ValueError):
        # One unreadable or unparsable file leaves only that file out of the map.
        return ()
