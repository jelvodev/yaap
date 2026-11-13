"""The update check: versions, the schedule, the service against a fake GitHub, and the API."""

import asyncio
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest

from app.updates import background, check as check_module
from app.updates.background import check_at_start, update_tick
from app.updates.changelog import changelog_section
from app.updates.check import PRIVATE_HINT, UpdateService
from app.updates.schedule import check_due, schedule_error
from app.updates.source import source_error
from app.updates.versions import is_newer, newest_tag, parse_tag, parse_version
from app.version import VERSION


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["v0.9.1", "v0.10.0", "v0.9.0"], "0.10.0"),
        (["v0.5.7", "v0.6.0-rc1", "v0.5.6"], "0.5.7"),
        (["latest", "0.6.0", "v1.2", "v1.2.3.4", "vx.y.z", "V1.0.0"], None),
        (["v1.0.0", "nightly", "v01.0.0"], "1.0.0"),
        ([], None),
    ],
)
def test_newest_tag_skips_pre_releases_and_malformed_tags(tags, expected):
    assert newest_tag(tags) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.2.3", (1, 2, 3)), ("0.10.0", (0, 10, 0)), ("1.2", None), ("1.2.3-rc1", None), ("v1.2.3", None), ("", None)],
)
def test_parse_version_is_strict(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize(("tag", "expected"), [("v1.2.3", (1, 2, 3)), ("1.2.3", None), ("v1.2.3-rc1", None)])
def test_parse_tag_needs_the_v(tag, expected):
    assert parse_tag(tag) == expected


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [("0.10.0", "0.9.0", True), ("0.5.7", "0.5.7", False), ("0.5.6", "0.5.7", False), ("junk", "0.5.7", False)],
)
def test_is_newer_compares_numbers_not_text(new, old, expected):
    assert is_newer(new, old) is expected


def test_changelog_section_is_none_without_the_version():
    text = (
        "# Changelog\n\n## Unreleased\n\nNone.\n\n## 0.6.0 (2026-12-01)\n\n- A change.\n\n"
        "## 0.5.7 (2026-11-09)\n\n- Old.\n"
    )
    assert changelog_section(text, "0.6.0") == "- A change."
    assert changelog_section(text, "0.7.0") is None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("0 9 * * *", False),
        ("off", False),
        ("0 9 * * 1", False),
        ("0 * * * *", False),
        ("*/30 * * * *", True),
        ("* * * * *", True),
        ("0,30 * * * *", True),
        ("0 9 * *", True),
        ("61 * * * *", True),
        ("", True),
        (5, True),
    ],
)
def test_schedule_refuses_intervals_under_an_hour(value, error):
    assert (schedule_error(value) is not None) is error


START = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("schedule", "last", "now", "due"),
    [
        ("0 9 * * *", None, datetime(2026, 11, 10, 8, 59, tzinfo=UTC), False),
        ("0 9 * * *", None, datetime(2026, 11, 10, 9, 0, 30, tzinfo=UTC), True),
        ("0 9 * * *", datetime(2026, 11, 10, 9, 0, 5, tzinfo=UTC), datetime(2026, 11, 10, 15, 0, tzinfo=UTC), False),
        ("0 9 * * *", datetime(2026, 11, 10, 9, 0, 5, tzinfo=UTC), datetime(2026, 11, 11, 9, 0, tzinfo=UTC), True),
        ("0 9 * * *", datetime(2026, 11, 10, 8, 5, tzinfo=UTC), datetime(2026, 11, 10, 9, 0, tzinfo=UTC), True),
        ("off", None, datetime(2026, 11, 20, 9, 0, tzinfo=UTC), False),
        ("*/5 * * * *", None, datetime(2026, 11, 20, 9, 0, tzinfo=UTC), False),
        ("not cron", None, datetime(2026, 11, 20, 9, 0, tzinfo=UTC), False),
    ],
)
def test_scheduled_check_is_due_after_the_last_check(schedule, last, now, due):
    assert check_due(schedule, "UTC", last, START, now) is due


def test_scheduled_check_follows_the_time_zone():
    now = datetime(2026, 11, 10, 8, 0, 30, tzinfo=UTC)  # 09:00 in Amsterdam
    assert check_due("0 9 * * *", "Europe/Amsterdam", None, datetime(2026, 11, 10, 7, 0, tzinfo=UTC), now) is True


NEWER = "9.9.9"
CHANGELOG = f"# Changelog\n\n## Unreleased\n\nNone.\n\n## {NEWER} (2099-01-01)\n\n### Action needed\n\nRun the script.\n"


class FakeGitHub:
    """A fake GitHub: answers /releases and the raw changelog, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tags: list[str] = [f"v{NEWER}", f"v{VERSION}"]
        self.status = 200
        self.etag = '"tags-1"'
        # Tags whose Release is a draft or a pre-release.
        self.unpublished: set[str] = set()
        self.offline = False
        self.notes_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("no network", request=request)
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(self.notes_status, text=CHANGELOG)
        if self.status != 200:
            return httpx.Response(self.status)
        if request.headers.get("If-None-Match") == self.etag:
            return httpx.Response(304)
        releases = [
            {"tag_name": tag, "draft": tag in self.unpublished, "prerelease": tag in self.unpublished}
            for tag in self.tags
        ]
        return httpx.Response(200, json=releases, headers={"ETag": self.etag})

    def tag_calls(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == "api.github.com"]


@pytest.fixture
def github(client) -> FakeGitHub:
    fake = FakeGitHub()
    client.app.state.updates.transport = httpx.MockTransport(fake)
    return fake


def service_of(client) -> UpdateService:
    return client.app.state.updates


def test_a_newer_release_is_found_with_its_notes_and_announced_once(client, github):
    first = service_of(client).check()
    assert first.state.newest_version == NEWER
    assert first.state.update_available and first.announce
    assert "Run the script." in first.state.notes
    second = service_of(client).check()
    assert second.state.newest_version == NEWER and not second.announce
    status = client.get("/api/v1/updates").json()
    assert status["update_available"] and status["announced_version"] == NEWER
    assert status["release_url"] == f"https://github.com/jelvodev/yaap/releases/tag/v{NEWER}"
    assert github.tag_calls()[0].headers["User-Agent"] == f"YAAP/{VERSION}"


def test_a_later_version_is_announced_again(client, github):
    service_of(client).check()
    github.tags.append("v99.0.0")
    github.etag = '"tags-2"'
    outcome = service_of(client).check()
    assert outcome.state.newest_version == "99.0.0" and outcome.announce


def test_the_same_version_is_not_an_update(client, github):
    github.tags = [f"v{VERSION}", "v0.0.1"]
    outcome = service_of(client).check()
    assert outcome.state.newest_version == VERSION
    assert not outcome.state.update_available and not outcome.announce


def test_not_modified_keeps_the_result_and_sends_the_etag(client, github):
    service_of(client).check()
    outcome = service_of(client).check()
    assert github.tag_calls()[1].headers["If-None-Match"] == github.etag
    assert outcome.state.newest_version == NEWER and outcome.state.error is None
    assert len([r for r in github.requests if r.url.host == "raw.githubusercontent.com"]) == 1


def test_a_failed_notes_read_is_retried_on_the_next_check(client, github):
    github.notes_status = 500
    first = service_of(client).check()
    assert first.state.newest_version == NEWER and first.state.notes == "" and first.state.etag is None
    github.notes_status = 200
    second = service_of(client).check()
    assert "If-None-Match" not in github.tag_calls()[1].headers
    assert "Run the script." in second.state.notes and second.state.etag == github.etag


def test_offline_keeps_the_last_result_and_shows_the_error(client, github):
    service_of(client).check()
    github.offline = True
    outcome = service_of(client).check()
    assert outcome.state.newest_version == NEWER
    assert outcome.state.error is not None and "cannot be reached" in outcome.state.error
    assert client.get("/api/v1/updates").json()["error"] == outcome.state.error


def test_malformed_tags_are_not_releases(client, github):
    github.tags = ["v1.0.0-rc1", "latest", "v2", "release-3.0.0"]
    outcome = service_of(client).check()
    assert outcome.state.newest_version is None and "no GitHub Release" in (outcome.state.error or "")
    assert not outcome.announce


@pytest.mark.parametrize(("unpublished", "expected"), [(set(), "99.0.0"), ({"v99.0.0"}, NEWER)])
def test_only_a_published_release_is_offered(client, github, unpublished, expected):
    github.tags.append("v99.0.0")
    github.unpublished = unpublished
    assert service_of(client).check().state.newest_version == expected


def test_a_missing_repository_without_a_token_says_a_token_is_needed(client, github):
    github.status = 404
    outcome = service_of(client).check()
    assert outcome.state.error == PRIVATE_HINT
    assert "Authorization" not in github.tag_calls()[0].headers


def test_a_missing_repository_with_a_token_does_not_blame_the_token_missing(client, github, monkeypatch):
    monkeypatch.setattr(check_module, "github_token_for", lambda *_: "ghp_test")
    github.status = 404
    outcome = service_of(client).check()
    assert "needs a GitHub token" not in (outcome.state.error or "")
    assert github.tag_calls()[0].headers["Authorization"] == "Bearer ghp_test"


def test_the_result_survives_a_restart_of_the_service(client, github):
    service_of(client).check()
    fresh = UpdateService(client.app.state.session_factory, client.app.state.cipher)
    assert fresh.current().newest_version == NEWER and fresh.current().announced_version == NEWER


def test_a_changed_source_is_asked_without_the_old_etag(client, github):
    service_of(client).check()
    assert client.put("/api/v1/settings/update_source", json={"value": "someone/fork"}).status_code == 200
    service_of(client).check()
    last = github.tag_calls()[-1]
    assert "/repos/someone/fork/releases" in str(last.url) and "If-None-Match" not in last.headers


@pytest.mark.parametrize("value", [".", "./yaap", "owner/..", "../..", "a/b/c", "owner"])
def test_the_update_source_refuses_dot_segments_and_other_shapes(value):
    assert source_error(value) is not None


def test_the_settings_refuse_a_bad_source_and_a_frequent_schedule(client):
    assert client.put("/api/v1/settings/update_source", json={"value": "not a repo"}).status_code == 422
    assert client.put("/api/v1/settings/update_check_schedule", json={"value": "*/10 * * * *"}).status_code == 422
    assert client.put("/api/v1/settings/update_check_schedule", json={"value": "off"}).status_code == 200


def test_off_means_no_call_at_start_or_on_the_schedule(client, github):
    client.put("/api/v1/settings/update_check_schedule", json={"value": "off"})
    asyncio.run(check_at_start(service_of(client), delay=0))
    assert update_tick(service_of(client), datetime(2099, 1, 1, 9, 0, tzinfo=UTC), wait=True) is False
    assert github.requests == []
    assert client.get("/api/v1/updates").json()["automatic"] is False
    # By hand still works.
    assert client.post("/api/v1/updates/check").json()["newest_version"] == NEWER
    assert github.requests


def test_on_means_a_check_at_start_and_on_the_schedule(client, github):
    asyncio.run(check_at_start(service_of(client), delay=0))
    assert len(github.tag_calls()) == 1
    assert update_tick(service_of(client), datetime(2099, 1, 1, 9, 0, tzinfo=UTC), wait=True) is True
    assert len(github.tag_calls()) == 2


def test_a_check_while_one_runs_waits_for_its_result(client):
    release = threading.Event()
    calls = []

    def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        release.wait(5)
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text=CHANGELOG)
        return httpx.Response(200, json=[{"tag_name": f"v{NEWER}"}])

    service = service_of(client)
    service.transport = httpx.MockTransport(slow)
    results = []
    first = threading.Thread(target=lambda: results.append(service.check()))
    first.start()
    while not calls:
        time.sleep(0.01)
    assert service.checking
    second = threading.Thread(target=lambda: results.append(service.check()))
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(5)
    second.join(5)
    assert [r.state.newest_version for r in results] == [NEWER, NEWER]
    assert len([c for c in calls if c.url.host == "api.github.com"]) == 1


def test_the_check_at_start_does_not_delay_the_app(tmp_path, monkeypatch, fake, master_key):
    from fastapi.testclient import TestClient

    from tests.conftest import make_test_app

    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path))
    release = threading.Event()
    called = threading.Event()
    returned = threading.Event()

    def hang(self: UpdateService) -> None:
        called.set()
        release.wait(10)
        returned.set()

    monkeypatch.setattr(UpdateService, "run_check", hang)
    monkeypatch.setattr(background.check, "START_DELAY_SECONDS", 0)
    app = make_test_app(fake)
    app.state.update_check_at_start = True
    try:
        with TestClient(app) as test_client:
            # The app answers while the check still hangs: the start does not wait for it. Timing the whole start
            # would also count the migrations, which take seconds on a slow disk.
            assert test_client.get("/api/v1/health").json() == {"status": "ok"}
            assert called.wait(5)
            assert test_client.get("/api/v1/health").json() == {"status": "ok"}
            assert not returned.is_set()
            release.set()
    finally:
        release.set()
