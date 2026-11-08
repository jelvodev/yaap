"""A small language server for the tests of app/lsp/ and the code tools: the subset YAAP uses, over stdio with
Content-Length framing, on plain regular expressions instead of a real analysis.

- initialize / initialized (it then asks the client for workspace/configuration, as pyright does), shutdown, exit;
- didOpen / didChange (full text) publish diagnostics: `name: int = "..."` (Python) or `let name: number = "..."`
  (TypeScript) is a type error;
- definition (a `def`, `class`, `function`, `interface`, `const` ... of the word), references (every whole-word use),
  rename (every whole-word use), documentSymbol (classes, functions and methods by indentation);
- the notification `fake/crash` ends the process at once, as a crash would.

Options: `--language python|typescript`; `--record FILE` writes the initialize parameters there; `--launch-record
FILE` first reads the launcher's base64 line (a server started in a runner) and writes the script there; `--map
SERVER=LOCAL` reads files under the server path SERVER from LOCAL on this computer.
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

WORD = re.compile(r"[\w$]+")
SUFFIXES = {"python": (".py",), "typescript": (".ts", ".tsx", ".js")}
DEFINITION = {
    "python": r"^\s*(?:async\s+)?(?:def|class)\s+{word}\b",
    "typescript": r"\b(?:function|class|interface|type|enum|const|let|var)\s+{word}\b",
}
TYPE_ERROR = re.compile(r"(\w+)\s*:\s*(int|number)\s*=\s*[\"']")
SYMBOL_LINE = {
    "python": re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)"),
    "typescript": re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(function|class|interface)\s+(\w+)|^(\s+)(\w+)\s*\("),
}

parser = argparse.ArgumentParser()
parser.add_argument("--language", default="python")
parser.add_argument("--record")
parser.add_argument("--launch-record")
parser.add_argument("--map")
options = parser.parse_args()
documents: dict[str, str] = {}
root = ""
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer


def server_path(uri: str) -> str:
    path = unquote(urlsplit(uri).path)
    return path[1:] if re.match(r"^/[A-Za-z]:", path) else path


def local_path(path: str) -> Path:
    if options.map:
        server, local = options.map.split("=", 1)
        if path == server or path.startswith(server + "/"):
            return Path(local + path[len(server) :])
    return Path(path)


def uri_of(path: str) -> str:
    path = path.replace("\\", "/")
    return "file://" + ("/" if re.match(r"^[A-Za-z]:", path) else "") + quote(path, safe="/:")


def read_message() -> dict | None:
    length = None
    while True:
        line = stdin.readline()
        if not line:
            return None
        if not line.strip():
            if length is not None:
                break
            continue
        name, _, value = line.decode("ascii").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    return json.loads(stdin.read(length).decode("utf-8"))


def send(message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    stdout.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stdout.flush()


def text_of(uri: str) -> str:
    if uri in documents:
        return documents[uri]
    return local_path(server_path(uri)).read_text(encoding="utf-8")


def project_files() -> list[str]:
    """URIs of the project's files of this language, open documents first as they are."""
    base = local_path(root)
    found = {uri_of(root + "/" + path.relative_to(base).as_posix()) for path in base.rglob("*")
             if path.suffix in SUFFIXES[options.language]}
    return sorted(found | {uri for uri in documents if uri.endswith(SUFFIXES[options.language])})


def word_at(uri: str, position: dict) -> str:
    line = text_of(uri).splitlines()[position["line"]]
    for match in WORD.finditer(line):
        if match.start() <= position["character"] <= match.end():
            return match.group(0)
    return ""


def occurrences(word: str) -> list[tuple[str, int, int]]:
    pattern = re.compile(rf"(?<![\w$]){re.escape(word)}(?![\w$])")
    found = []
    for uri in project_files():
        for number, line in enumerate(text_of(uri).splitlines()):
            found += [(uri, number, match.start()) for match in pattern.finditer(line)]
    return found


def span(line: int, start: int, length: int) -> dict:
    return {"start": {"line": line, "character": start}, "end": {"line": line, "character": start + length}}


def definition(params: dict) -> list[dict]:
    word = word_at(params["textDocument"]["uri"], params["position"])
    pattern = re.compile(DEFINITION[options.language].format(word=re.escape(word)))
    for uri in project_files():
        for number, line in enumerate(text_of(uri).splitlines()):
            if pattern.search(line):
                return [{"uri": uri, "range": span(number, line.index(word), len(word))}]
    return []


def references(params: dict) -> list[dict]:
    word = word_at(params["textDocument"]["uri"], params["position"])
    return [{"uri": uri, "range": span(line, start, len(word))} for uri, line, start in occurrences(word)]


def rename(params: dict) -> dict:
    word = word_at(params["textDocument"]["uri"], params["position"])
    changes: dict[str, list] = {}
    for uri, line, start in occurrences(word):
        changes.setdefault(uri, []).append({"range": span(line, start, len(word)), "newText": params["newName"]})
    return {"changes": changes}


def symbols(params: dict) -> list[dict]:
    lines = text_of(params["textDocument"]["uri"]).splitlines()
    found: list[tuple[int, dict]] = []
    for number, line in enumerate(lines):
        match = SYMBOL_LINE[options.language].match(line)
        if not match or (match.group(match.lastindex or 0) in ("if", "for", "while", "return")):
            continue
        groups = [group for group in match.groups()]
        indent = len(groups[0] if groups[0] is not None else groups[3] or "")
        keyword = groups[1] or "method"
        name = groups[2] or groups[4]
        kind = {"class": 5, "interface": 11}.get(keyword, 6 if indent else 12)
        end = number
        for later in range(number + 1, len(lines)):
            if lines[later].strip() and len(lines[later]) - len(lines[later].lstrip()) <= indent:
                break
            if lines[later].strip():
                end = later
        symbol = {"name": name, "kind": kind, "range": span(number, 0, 0) | {"end": {"line": end, "character": 0}},
                  "selectionRange": span(number, line.index(name), len(name)), "children": []}
        found.append((indent, symbol))
    top: list[dict] = []
    parents: list[tuple[int, dict]] = []
    for indent, symbol in found:
        while parents and parents[-1][0] >= indent:
            parents.pop()
        (parents[-1][1]["children"] if parents else top).append(symbol)
        parents.append((indent, symbol))
    return top


def publish(uri: str, version: int) -> None:
    diagnostics = []
    for number, line in enumerate(documents[uri].splitlines()):
        match = TYPE_ERROR.search(line)
        if match:
            expected = match.group(2)
            diagnostics.append({
                "range": span(number, match.start(), match.end() - match.start()),
                "severity": 1,
                "source": "fake",
                "message": f'Type "str" is not assignable to declared type "{expected}"',
            })
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "version": version, "diagnostics": diagnostics}})


REQUESTS = {
    "textDocument/definition": definition,
    "textDocument/references": references,
    "textDocument/rename": rename,
    "textDocument/documentSymbol": symbols,
}


def main() -> None:
    global root
    if options.launch_record:
        script = base64.b64decode(stdin.readline().strip()).decode("utf-8")
        Path(options.launch_record).write_text(script, encoding="utf-8")
    while (message := read_message()) is not None:
        method, params = message.get("method"), message.get("params") or {}
        if method is None:
            continue  # An answer from the client (to workspace/configuration).
        if method == "initialize":
            root = server_path(params["rootUri"])
            if options.record:
                Path(options.record).write_text(json.dumps(params), encoding="utf-8")
            capabilities = {"textDocumentSync": 1, "definitionProvider": True, "referencesProvider": True,
                            "renameProvider": True, "documentSymbolProvider": True}
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": capabilities}})
        elif method == "initialized":
            send({"jsonrpc": "2.0", "id": "config-1", "method": "workspace/configuration",
                  "params": {"items": [{"section": "python"}]}})
            send({"jsonrpc": "2.0", "method": "window/logMessage", "params": {"type": 3, "message": "ready"}})
        elif method == "textDocument/didOpen":
            item = params["textDocument"]
            documents[item["uri"]] = item["text"]
            publish(item["uri"], item["version"])
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            documents[uri] = params["contentChanges"][-1]["text"]
            publish(uri, params["textDocument"]["version"])
        elif method == "textDocument/didClose":
            documents.pop(params["textDocument"]["uri"], None)
        elif method == "fake/crash":
            sys.stderr.write("fake server: crashing as asked\n")
            sys.stderr.flush()
            os._exit(3)
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": message["id"], "result": None})
        elif method == "exit":
            sys.exit(0)
        elif method in REQUESTS:
            send({"jsonrpc": "2.0", "id": message["id"], "result": REQUESTS[method](params)})
        elif "id" in message:
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"No {method}"}})


main()
