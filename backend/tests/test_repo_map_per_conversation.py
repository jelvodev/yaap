"""The repository map kept per conversation: the kept key, and a reply reusing the first reply's map."""

import pytest

from app.core.reply_repo_map import kept_map_key
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Message
from tests.test_chat import send
from tests.test_delegation import project_conversation
from tests.test_repo_map import write

TEXT = {"type": "text", "text": "hi"}
HERE = "container::/workspace/stockroom"
MAP = {"type": "repo_map", "key": "v1:1500:git:a:clean", "location": HERE}
NEWER_MAP = {"type": "repo_map", "key": "v1:1500:git:b:dirty", "location": HERE}
ELSEWHERE_MAP = {"type": "repo_map", "key": "v1:1500:git:c:clean", "location": "container::/workspace/other"}
TRIM = {"type": "trim"}
SUMMARY = {"type": "summary", "text": "so far"}


def branch_of(*contents: list[dict]) -> list[Message]:
    return [Message(role="user", content=list(content)) for content in contents]


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ([], None),
        ([[TEXT], [TEXT]], None),
        ([[TEXT, MAP], [TEXT]], MAP["key"]),
        ([[TEXT, MAP], [TEXT, TRIM], [TEXT, NEWER_MAP]], NEWER_MAP["key"]),
        ([[TEXT, MAP], [TEXT, TRIM], [TEXT]], None),
        ([[TEXT, MAP], [TEXT, SUMMARY], [TEXT]], None),
        ([[TEXT], [TEXT, TRIM, SUMMARY], [TEXT, MAP]], MAP["key"]),
        ([[TEXT, MAP], [TEXT], [TEXT, NEWER_MAP]], NEWER_MAP["key"]),
        # A block on the message that carries the trim is not after it.
        ([[TEXT, MAP, TRIM], [TEXT]], None),
        # A map made for another folder is not kept, nor an older one behind it.
        ([[TEXT, MAP], [TEXT, ELSEWHERE_MAP]], None),
    ],
)
def test_the_kept_key_is_the_newest_block_after_the_latest_trim_or_summary(
    contents: list[list[dict[str, str]]], expected: str | None
) -> None:
    assert kept_map_key(branch_of(*contents), HERE) == expected


def map_texts(fake) -> list[str]:
    return [next(part.text for part in request.system if part.source == "repo_map") for request in fake.requests]


@pytest.mark.parametrize(("setting", "same"), [("on", True), ("off", False)])
def test_a_later_reply_keeps_the_first_map_only_with_context_savings_on(client, fake, setting, same):
    with client.app.state.session_factory() as session:
        store_setting(session, SETTING_DEFINITIONS["context_savings"], None, setting)
    project, conversation_id = project_conversation(client)
    folder = client.app.state.workspace_dir / project["folder"]
    write(folder, {"shop/cart.py": "class Cart:\n    def add(self, item):\n        pass\n"})
    fake.responses.extend([ScriptedResponse(text="One."), ScriptedResponse(text="Two.")])
    send(client, conversation_id, "Hello")
    write(folder, {"shop/order.py": "class Order:\n    def place(self):\n        pass\n"})
    send(client, conversation_id, "Again")
    first, second = map_texts(fake)
    assert "shop/order.py" not in first
    assert (first == second) is same
    assert ("shop/order.py" in second) is not same
    blocks = [
        block
        for message in client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
        for block in message["content"]
        if block["type"] == "repo_map"
    ]
    assert len(blocks) == (1 if same else 0)
