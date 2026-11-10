"""Speech: transcription against a stubbed model, the model download, and the text read aloud."""

import hashlib
import io
import math
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from app.speech.languages import guess_language, language_code
from app.speech.reading import reply_text, speakable_text
from app.speech.service import SpeechService
from app.core.model_files import ModelFile, RemoteModel
from app.speech.whisper import decode_audio
from app.storage.models import Message
from tests.test_chat import start_conversation


def wav_sample(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    """A short generated recording: a 440 Hz tone as 16-bit mono WAV."""
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * index / rate))) for index in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def decode_wav(data: bytes) -> np.ndarray:
    """Stands in for PyAV, so these tests run without it."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class StubTranscriber:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def transcribe(self, samples: np.ndarray, language: str | None) -> tuple[str, str | None]:
        self.calls.append((len(samples), language))
        return "Open the readme, please.", language or "en"


FILES = {"config.json": b"{}", "model.bin": b"weights" * 100, "vocabulary.txt": b"a\nb\n"}


def fake_models() -> dict[str, RemoteModel]:
    files = tuple(ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in FILES.items())
    return {name: RemoteModel(name, f"test/{name}", "0" * 40, files) for name in ("small", "small.en")}


def fake_fetch(fetched: list[str], content: dict[str, bytes] = FILES):
    def fetch(url: str, destination: Path, expected: ModelFile, progress) -> None:
        fetched.append(url)
        data = content[expected.name]
        destination.write_bytes(data)
        progress(len(data))
        if hashlib.sha256(data).hexdigest() != expected.sha256:
            raise ValueError(f"{expected.name} does not match its pinned SHA-256.")

    return fetch


def install(tmp_path: Path, monkeypatch=None, *, ready: bool = True, available: bool = True, content=FILES):
    calls: list = []
    fetched: list[str] = []
    loads: list[Path] = []

    def load(folder: Path) -> StubTranscriber:
        loads.append(folder)
        return StubTranscriber(calls)

    service = SpeechService(
        tmp_path / "models" / "whisper",
        models=fake_models(),
        load_model=load,
        fetch=fake_fetch(fetched, content),
        decode=decode_wav,
        available=lambda: available,
    )
    if ready:
        for name in ("small", "small.en"):
            folder = service.model_dir(name)
            folder.mkdir(parents=True)
            for file_name, data in FILES.items():
                (folder / file_name).write_bytes(data)
    return service, calls, fetched, loads


def post_audio(client, data: bytes, project_id: int | None = None):
    query = f"?project_id={project_id}" if project_id else ""
    return client.post(
        f"/api/v1/environments/1/speech/transcriptions{query}",
        files={"audio": ("recording.webm", data, "audio/webm")},
    )


def test_a_recording_becomes_text_in_the_reply_language(client, tmp_path):
    service, calls, _, loads = install(tmp_path)
    client.app.state.speech = service
    client.put("/api/v1/settings/reply_language", json={"value": "Dutch"})

    response = post_audio(client, wav_sample(1.0))
    assert response.status_code == 200
    assert response.json() == {"text": "Open the readme, please.", "language": "nl", "seconds": 1.0, "model": "small"}
    assert calls == [(16_000, "nl")]

    # A second recording reuses the loaded model.
    post_audio(client, wav_sample(0.5))
    assert len(loads) == 1


def test_english_replies_use_the_english_model_and_a_project_level(client, tmp_path):
    service, calls, _, _ = install(tmp_path)
    client.app.state.speech = service
    project = client.post("/api/v1/environments/1/projects", json={"name": "Docs"}).json()
    client.put(f"/api/v1/projects/{project['id']}/settings/reply_language", json={"value": "English"})

    assert post_audio(client, wav_sample(), project["id"]).json()["model"] == "small.en"
    assert calls[-1][1] == "en"
    # Without a reply language Whisper detects the language itself.
    assert post_audio(client, wav_sample()).json()["model"] == "small"
    assert calls[-1][1] is None
    assert post_audio(client, wav_sample(), 999).status_code == 404


def test_too_short_or_unreadable_audio_is_refused(client, tmp_path):
    service, calls, _, _ = install(tmp_path)
    client.app.state.speech = service
    response = post_audio(client, wav_sample(0.1))
    assert response.status_code == 422 and "too short" in response.json()["detail"]
    assert calls == []


def test_the_model_is_downloaded_on_first_use(client, tmp_path):
    service, calls, fetched, _ = install(tmp_path, ready=False)
    client.app.state.speech = service

    status = client.get("/api/v1/environments/1/speech").json()
    assert status["state"] == "missing" and status["model"] == "small"
    assert "downloaded once" in status["message"]

    # Recording before the model is there starts the download and says so.
    response = post_audio(client, wav_sample())
    assert response.status_code == 409
    service.wait()
    assert sorted(url.rsplit("/", 1)[1] for url in fetched) == sorted(FILES)
    assert all("/resolve/" + "0" * 40 + "/" in url for url in fetched)

    status = client.get("/api/v1/environments/1/speech").json()
    assert status["state"] == "ready" and status["downloaded"] == status["size"]
    assert not (service.folder / "small.download").exists()
    assert post_audio(client, wav_sample()).status_code == 200
    # Asking again for a model that is there downloads nothing.
    client.post("/api/v1/environments/1/speech/download")
    service.wait()
    assert len(fetched) == len(FILES)


def test_a_download_that_does_not_match_its_hash_is_thrown_away(client, tmp_path):
    service, _, _, _ = install(tmp_path, ready=False, content={**FILES, "model.bin": b"tampered"})
    client.app.state.speech = service

    client.post("/api/v1/environments/1/speech/download")
    service.wait()
    status = client.get("/api/v1/environments/1/speech").json()
    assert status["state"] == "failed" and "SHA-256" in status["message"]
    assert not service.model_dir("small").exists() and not (service.folder / "small.download").exists()


def test_without_faster_whisper_speech_input_says_it_is_unavailable(client, tmp_path):
    service, _, fetched, _ = install(tmp_path, ready=False, available=False)
    client.app.state.speech = service
    status = client.post("/api/v1/environments/1/speech/download").json()
    assert status["state"] == "unavailable" and "not installed" in status["message"]
    response = post_audio(client, wav_sample())
    assert response.status_code == 503
    assert fetched == []


def test_the_default_service_starts_without_a_model(client):
    status = client.get("/api/v1/environments/1/speech").json()
    assert status["state"] in ("missing", "unavailable")
    assert status["size"] > 400_000_000


def test_pyav_decodes_a_recording_to_16_khz():
    pytest.importorskip("av")
    samples = decode_audio(wav_sample(1.0, rate=48_000))
    assert samples.dtype == np.float32
    assert abs(len(samples) - 16_000) < 400
    with pytest.raises(ValueError):
        decode_audio(b"not audio at all")


REPLY = """## Plan

I changed **two** files; see [the guide](https://example.com/guide) and https://example.com/raw.

```python
print("never read")
```

- Run `pytest` first
- Then *deploy*

| Name | Size |
|---|---|
| model | 484 MB |

![diagram](chart.png)
~~~
also code
~~~
Done!"""


def test_reading_aloud_leaves_out_code_and_marks():
    text = speakable_text(REPLY)
    assert "print" not in text and "also code" not in text and "never read" not in text
    assert "https" not in text and "chart.png" not in text and "diagram" not in text
    assert "*" not in text and "`" not in text and "#" not in text and "|" not in text
    assert text.split("\n\n") == [
        "Plan.",
        "I changed two files; see the guide and.",
        "Run pytest first.",
        "Then deploy.",
        "Name, Size.",
        "model, 484 MB.",
        "Done!",
    ]


def test_snake_case_and_an_unclosed_code_block():
    assert speakable_text("Use read_output now.\n```\nhalf a block") == "Use read_output now."


def test_a_reply_is_read_without_thinking_or_tool_calls(client):
    conversation_id = start_conversation(client)
    content = [
        {"type": "reasoning", "text": "Let me think about the files."},
        {"type": "text", "text": "Ik kijk even in de map."},
        {"type": "tool_call", "id": "1", "name": "run_command", "input": {"command": "ls"}, "output": "secret.txt"},
        {"type": "text", "text": "Het bestand is er niet, maar je kunt het maken.\n\n```bash\ntouch x\n```"},
    ]
    assert reply_text(content) == "Ik kijk even in de map.\n\nHet bestand is er niet, maar je kunt het maken.\n\n```bash\ntouch x\n```"
    with client.app.state.session_factory() as session:
        message = Message(conversation_id=conversation_id, role="assistant", content=content)
        question = Message(conversation_id=conversation_id, role="user", content=[{"type": "text", "text": "Hi"}])
        session.add_all([message, question])
        session.commit()
        message_id, question_id = message.id, question.id

    body = client.get(f"/api/v1/messages/{message_id}/speech").json()
    assert body["text"] == "Ik kijk even in de map.\n\nHet bestand is er niet, maar je kunt het maken."
    assert "think" not in body["text"] and "ls" not in body["text"] and "secret" not in body["text"]
    # No reply language set: guessed from the words.
    assert body["language"] == "nl"
    client.put("/api/v1/environments/1/settings/reply_language", json={"value": "German"})
    assert client.get(f"/api/v1/messages/{message_id}/speech").json()["language"] == "de"
    assert client.get(f"/api/v1/messages/{question_id}/speech").status_code == 404


def test_language_names_and_guesses():
    assert language_code("Dutch") == language_code("Nederlands") == language_code(" nl ") == "nl"
    assert language_code("Portuguese (Brazil)") == "pt"
    assert language_code("Klingon") is None and language_code("") is None
    assert guess_language("This is the plan and you can run it with the tests.") == "en"
    assert guess_language("Dit is het plan en je kunt het ook niet zomaar doen.") == "nl"
    assert guess_language("ok") is None
