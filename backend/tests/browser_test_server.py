"""A stand-in for Playwright MCP in the tests: the same tool names, pages as text, a tiny screenshot."""

import json
import sys

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
NAMES = [
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_take_screenshot",
    "browser_evaluate",
    "browser_file_upload",
]
TOOLS = [{"name": name, "description": f"Playwright {name}", "inputSchema": {"type": "object", "properties": {}}} for name in NAMES]
state = {"url": "about:blank", "typed": ""}


def reply(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def page():
    return f"- Page URL: {state['url']}\n- Page Title: Test\n```yaml\n- textbox \"Password\" [ref=e2]: {state['typed']}\n- button \"Log in\" [ref=e3]\n```"


for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        reply(message["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "pw", "version": "1"}})
    elif method == "tools/list":
        reply(message["id"], {"tools": TOOLS})
    elif method == "tools/call":
        name, arguments = params["name"], params.get("arguments") or {}
        if name == "browser_navigate":
            state["url"] = arguments["url"]
        if name == "browser_type":
            state["typed"] = arguments.get("text", "")
        content = [{"type": "text", "text": f"### Ran {name}\n{page()}"}]
        if name == "browser_take_screenshot":
            content.append({"type": "image", "data": PNG, "mimeType": "image/png"})
        reply(message["id"], {"content": content, "isError": False})
