"""A stand-in MCP server over stdio that lists the tools of a JSON file, for YAAP's token footprint (plan 46.1).

The footprint measures what YAAP sends to a model; for the browser that is the definitions of Playwright MCP's
tools, which need Node.js and a browser to list for real. This server lists them from `playwright_tools.json` (or
the file in the environment variable STAND_IN_TOOLS) and answers every call with a short fixed text. Standard library
only.
"""

import json
import os
import sys
from pathlib import Path

TOOLS_FILE = Path(__file__).with_name("playwright_tools.json")


def respond(message_id: object, result: object) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    # Playwright MCP's own arguments (--headless and the like) are passed along by YAAP and ignored here.
    path = Path(os.environ.get("STAND_IN_TOOLS") or TOOLS_FILE)
    tools = json.loads(path.read_text(encoding="utf-8"))["tools"]
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message.get("method")
        if method == "initialize":
            info = {"name": "stand-in", "version": "1"}
            respond(message["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": info})
        elif method == "tools/list":
            respond(message["id"], {"tools": tools})
        elif method == "tools/call":
            respond(message["id"], {"content": [{"type": "text", "text": "(stand-in)"}], "isError": False})
        else:
            respond(message["id"], {})


if __name__ == "__main__":
    main()
