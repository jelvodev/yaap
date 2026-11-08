"""Shutting YAAP down (app/core/shutdown.py, background.py): work that replies leave running ends before the database
engine goes, also when it waits on a worker thread."""

import asyncio
import threading
import time

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import Pool

from app.core.background import BackgroundWork
from app.core.reply_events import BACKGROUND_WORK
from app.core.shutdown import AFTER_REPLIES
from app.core.summaries import SUMMARIES
from app.learning.after_reply import LEARNING_REVIEWS
from app.projects.consolidation import TIDY_CHECKS
from app.reviews.service import REVIEWS
from app.tracing.otlp_export import EXPORTS
from tests.conftest import FakeBackend, make_test_app

WORK = {
    "reply follow-up": BACKGROUND_WORK,
    "review": REVIEWS,
    "summary": SUMMARIES,
    "learning review": LEARNING_REVIEWS,
    "memory tidy check": TIDY_CHECKS,
    "telemetry export": EXPORTS,
}
KINDS = pytest.mark.parametrize("work", list(WORK.values()), ids=list(WORK))


def test_shutdown_stops_every_kind_of_work_that_replies_start() -> None:
    assert set(map(id, AFTER_REPLIES)) == set(map(id, WORK.values()))


@pytest.fixture
def data_dir(tmp_path, monkeypatch, master_key: str):
    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path))
    return tmp_path


@KINDS
def test_work_a_reply_left_running_ends_before_the_database_engine_goes(
    data_dir, fake: FakeBackend, work: BackgroundWork[None]
) -> None:
    app = make_test_app(fake)
    pools_when_ended: list[Pool] = []
    with TestClient(app) as client:
        engine = app.state.session_factory.kw["bind"]
        pool = engine.pool

        async def follow_up() -> None:
            try:
                await asyncio.sleep(3600)
            finally:
                pools_when_ended.append(engine.pool)

        async def start() -> None:
            work.start(follow_up())

        client.portal.call(start)

    # It was stopped while the engine still had its pool: a connection it held went with that pool.
    assert pools_when_ended == [pool]


@KINDS
def test_work_in_a_worker_thread_finishes_its_thread_before_the_database_engine_goes(
    data_dir, fake: FakeBackend, work: BackgroundWork[None]
) -> None:
    app = make_test_app(fake)
    in_thread = threading.Event()
    pools_when_thread_ended: list[Pool] = []
    with TestClient(app) as client:
        engine = app.state.session_factory.kw["bind"]
        pool = engine.pool

        def store() -> None:
            in_thread.set()
            time.sleep(0.5)
            pools_when_thread_ended.append(engine.pool)

        async def follow_up() -> None:
            await anyio.to_thread.run_sync(store)
            await asyncio.sleep(3600)

        async def start() -> None:
            work.start(follow_up())

        client.portal.call(start)
        assert in_thread.wait(5)

    # Task.cancel() would have left the thread running past the engine's disposal; the cancel scope waited for it.
    assert pools_when_thread_ended == [pool]
