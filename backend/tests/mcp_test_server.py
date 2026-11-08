"""A tiny MCP server over stdio for the tests. Tools: echo, env (reads a variable), slow, fail."""

import json
import os
import sys
import time

TOOLS = [
    {"name": "echo", "description": "Repeat the text.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {
        "name": "env",
        "description": "Read an environment variable.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {"name": "slow", "description": "Sleep for a while.", "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hidden thing", "description": "A tool with an awkward name.", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def text(value, error=False):
    return {"content": [{"type": "text", "text": value}], "isError": error}


print("test server starting", file=sys.stderr, flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        reply(message["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}})
    elif method == "tools/list":
        reply(message["id"], {"tools": TOOLS})
    elif method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name == "echo":
            reply(message["id"], text(f"echo: {arguments.get('text', '')} (pid {os.getpid()})"))
        elif name == "env":
            reply(message["id"], text(os.environ.get(arguments.get("name", ""), "(unset)")))
        elif name == "slow":
            time.sleep(float(arguments.get("seconds", 5)))
            reply(message["id"], text("slept"))
        elif name == "fail":
            reply(message["id"], text("it broke", error=True))
        else:
            reply(message["id"], text("hidden"))
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "no"}}) + "\n")
        sys.stdout.flush()
