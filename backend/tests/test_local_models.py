"""The downloaded models: the shared downloader, the model service, the API, and search while the model is missing."""

import asyncio
import hashlib
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.embeddings import BGE_M3, model_folder
from app.core.local_models import SEARCH_MODEL, ModelBusy, ModelEntry, ModelService, UnknownModel
from app.core.model_files import ModelDownloads, ModelFile, RemoteModel, install_model, is_complete
from app.core.search_model import start_automatic_download, start_when_first_start_is_done
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import SearchChunk
from tests.test_blueprint import put
from tests.test_chat import add_key, send, start_conversation
from tests.test_semantic_search import WordEmbedder, chunk_count, index, search

DATA = {"model.onnx": b"graph" * 20, "tokenizer.json": b'{"tokens": []}'}


def tiny_model() -> RemoteModel:
    files = tuple(ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in DATA.items())
    return RemoteModel("tiny", "test/tiny", "1" * 40, files)


class FakeHub:
    """Stands in for Hugging Face: serves `content` per file name, and can fail part way through a file."""

    def __init__(self, content: dict[str, bytes] = DATA, interrupt: str | None = None) -> None:
        self.content = content
        self.interrupt = interrupt
        self.requested: list[str] = []

    def __call__(self, url: str, destination: Path, expected: ModelFile, progress) -> None:
        self.requested.append(url)
        data = self.content[expected.name]
        half = len(data) // 2
        destination.write_bytes(data[:half])
        progress(half)
        if expected.name == self.interrupt:
            raise ConnectionError("The connection was lost.")
        destination.write_bytes(data)
        progress(len(data) - half)
        if hashlib.sha256(data).hexdigest() != expected.sha256:
            raise ValueError(f"{expected.name} does not match its pinned SHA-256.")


@pytest.mark.parametrize(
    ("hub", "error"),
    [
        pytest.param(FakeHub(), None, id="good files"),
        pytest.param(FakeHub({**DATA, "model.onnx": b"tampered"}), "SHA-256", id="wrong hash"),
        pytest.param(FakeHub(interrupt="tokenizer.json"), "connection was lost", id="interrupted download"),
    ],
)
def test_install_keeps_only_complete_and_matching_files(tmp_path, hub, error):
    model, target = tiny_model(), tmp_path / "tiny"
    progress: list[int] = []
    if error is None:
        install_model(model, target, hub, progress.append)
    else:
        with pytest.raises(Exception, match=error):
            install_model(model, target, hub, progress.append)
    assert is_complete(model, target) is (error is None)
    assert not (tmp_path / "tiny.download").exists()
    if error is None:
        assert sum(progress) == model.size


def test_a_model_that_is_there_is_not_downloaded_again(tmp_path):
    hub = FakeHub()
    service = fake_service(tmp_path, hub)
    service.start_download(SEARCH_MODEL)
    service.downloads.wait()
    assert hub.requested and service.status(SEARCH_MODEL).state == "ready"
    hub.requested.clear()
    assert service.start_download(SEARCH_MODEL).state == "ready"
    assert hub.requested == []


def test_the_pinned_search_model_has_the_files_the_image_used_to_hold():
    names = [item.name for item in BGE_M3.files]
    assert names == ["model.onnx", "model.onnx_data", "Constant_7_attr__value", "tokenizer.json"]
    assert all(len(item.sha256) == 64 for item in BGE_M3.files)
    assert BGE_M3.url(BGE_M3.files[0]).endswith(f"/resolve/{BGE_M3.revision}/onnx/model.onnx")
    assert 2.2e9 < BGE_M3.size < 2.4e9


def fake_service(tmp_path, hub: FakeHub | None = None, changes: list[str] | None = None) -> ModelService:
    service = ModelService(
        tmp_path / "models",
        ModelDownloads(hub or FakeHub()),
        on_change=(changes if changes is not None else []).append,
    )
    service.entries[SEARCH_MODEL] = ModelEntry(SEARCH_MODEL, "Search", "Search", tiny_model(), tmp_path / "bge")
    return service


def test_the_service_goes_from_not_downloaded_to_ready_and_back(tmp_path):
    changes: list[str] = []
    service = fake_service(tmp_path, changes=changes)
    assert service.status(SEARCH_MODEL).state == "not_downloaded"
    service.start_download(SEARCH_MODEL)
    service.downloads.wait()
    ready = service.status(SEARCH_MODEL)
    assert ready.state == "ready" and ready.downloaded == ready.size
    assert changes == [SEARCH_MODEL]
    assert service.remove(SEARCH_MODEL).state == "not_downloaded"
    assert changes == [SEARCH_MODEL, SEARCH_MODEL]


def test_the_service_reports_a_failed_download_with_its_reason(tmp_path):
    service = fake_service(tmp_path, FakeHub(interrupt="model.onnx"))
    service.start_download(SEARCH_MODEL)
    service.downloads.wait()
    status = service.status(SEARCH_MODEL)
    assert status.state == "failed" and "connection was lost" in status.message


def test_only_one_download_runs_per_model_and_a_running_one_is_not_removed(tmp_path):
    release = threading.Event()
    inner = FakeHub()

    def slow(url, destination, expected, progress):
        release.wait(5)
        inner(url, destination, expected, progress)

    service = fake_service(tmp_path)
    service.downloads.fetch = slow
    first = service.start_download(SEARCH_MODEL)
    second = service.start_download(SEARCH_MODEL)
    assert first.state == second.state == "downloading"
    with pytest.raises(ModelBusy):
        service.remove(SEARCH_MODEL)
    release.set()
    service.downloads.wait()
    assert len(inner.requested) == len(DATA)
    with pytest.raises(UnknownModel):
        service.status("nope")


def test_the_search_model_lives_in_the_data_folder_next_to_the_speech_models(tmp_path):
    service = ModelService(tmp_path / "models")
    assert service.entries[SEARCH_MODEL].folder == model_folder(tmp_path / "models")
    assert service.entries[SEARCH_MODEL].folder.parts[-2:] == ("bge-m3", BGE_M3.revision)
    assert {"bge-m3", "whisper-small", "whisper-small.en"} <= set(service.entries)


# --- The API ---------------------------------------------------------------------------------------------------


def use_fake_models(client: TestClient, tmp_path, hub: FakeHub | None = None) -> ModelService:
    service = fake_service(tmp_path, hub)
    client.app.state.models = service
    return service


def test_the_models_api_needs_a_sign_in(client):
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/local-models").status_code == 401
    assert anonymous.post("/api/v1/local-models/bge-m3/download").status_code == 401
    assert anonymous.delete("/api/v1/local-models/bge-m3").status_code == 401


def test_the_models_api_lists_downloads_and_removes(client, tmp_path):
    service = use_fake_models(client, tmp_path)
    response = client.get("/api/v1/local-models")
    assert response.status_code == 200, response.text
    listed = {item["name"]: item for item in response.json()}
    assert listed[SEARCH_MODEL]["state"] == "not_downloaded"
    assert listed["whisper-small"]["size"] > 400_000_000

    assert client.post("/api/v1/local-models/bge-m3/download").status_code == 200
    service.downloads.wait()
    assert client.get("/api/v1/local-models").json()[0]["state"] == "ready"
    assert client.delete("/api/v1/local-models/bge-m3").json()["state"] == "not_downloaded"
    assert client.post("/api/v1/local-models/unknown/download").status_code == 404
    assert client.delete("/api/v1/local-models/unknown").status_code == 404


# --- Search while the model is missing --------------------------------------------------------------------------


def test_words_are_found_and_meaning_is_flagged_until_the_model_is_ready(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Use SQLite as the database for the shop."))
    send(client, conversation_id, "What should we use?")
    indexer = client.app.state.indexer

    assert client.get("/api/v1/search-index/status").json()["meaning"] == "not_ready"
    assert indexer.index_pass() == 0  # Messages wait for the model.
    assert [hit["kind"] for hit in search(client, "SQLite")] == ["message"]  # Words work meanwhile.
    assert search(client, "storage") == []  # Meaning does not, and nothing fails.

    # The model arrives: the next pass indexes what waited, and meaning finds it.
    indexer.load_embedder = WordEmbedder
    indexer.reload_embedder()
    assert client.get("/api/v1/search-index/status").json()["meaning"] == "ready"
    assert client.get("/api/v1/search-index/status").json()["waiting_messages"] == 2
    assert index(client) == 2
    assert chunk_count(client, "message") == 2
    assert search(client, "storage")


def test_memory_waits_without_vectors_and_is_embedded_once_the_model_is_ready(client):
    put(client, "standards/storage.md", "---\nsummary: Storage\n---\n\nKeep the database small.\n", environment_id=1)
    assert index(client) >= 1
    with client.app.state.session_factory() as session:
        assert session.scalar(select(SearchChunk.model).limit(1)) is None
    indexer = client.app.state.indexer
    indexer.load_embedder = WordEmbedder
    indexer.reload_embedder()
    assert index(client) >= 1
    with client.app.state.session_factory() as session:
        assert session.scalar(select(SearchChunk.model).limit(1)) == "test-words"


# --- The automatic download -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "embedding", "model_there", "starts"),
    [
        pytest.param(None, None, False, True, id="on by default"),
        pytest.param("on", None, False, True, id="switched on"),
        pytest.param("off", None, False, False, id="switched off"),
        pytest.param(None, "off", False, False, id="search by meaning is off"),
        pytest.param(None, None, True, False, id="the model is there"),
    ],
)
def test_the_automatic_download_follows_the_setting(client, tmp_path, setting, embedding, model_there, starts):
    hub = FakeHub()
    service = use_fake_models(client, tmp_path, hub)
    if model_there:
        service.start_download(SEARCH_MODEL)
        service.downloads.wait()
        hub.requested.clear()
    for key, value in (("search_model_download", setting), ("embedding_model", embedding)):
        if value:
            assert client.put(f"/api/v1/settings/{key}", json={"value": value}).status_code == 200
    with client.app.state.session_factory() as session:
        assert start_automatic_download(session, service) is starts
    service.downloads.wait()
    assert bool(hub.requested) is starts


def test_finishing_the_first_start_starts_the_download(client, tmp_path):
    hub = FakeHub()
    service = use_fake_models(client, tmp_path, hub)
    client.post("/api/v1/first-run/start")
    assert hub.requested == []
    client.post("/api/v1/first-run/finish")
    service.downloads.wait()
    assert service.status(SEARCH_MODEL).state == "ready"


def test_the_download_at_application_start_waits_for_the_first_start(client, tmp_path):
    hub = FakeHub()
    service = use_fake_models(client, tmp_path, hub)
    state = client.app.state

    def run() -> None:
        asyncio.run(
            start_when_first_start_is_done(state.first_run, service, state.session_factory, state.blueprint_dir)
        )
        service.downloads.wait()

    # A fresh installation: the wizard is still to come.
    run()
    assert hub.requested == []
    # Once the first start is over the model comes, also after an update of an older installation.
    with state.session_factory() as session:
        state.first_run.finish(session)
    run()
    assert service.status(SEARCH_MODEL).state == "ready"
