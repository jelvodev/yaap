"""Stands in for bash in a runner when YAAP launches an MCP server there: it reads the launch line as the
runner's launcher does, records the script in the file named by its first argument, sets the variables the script
exports and then serves as tests/mcp_test_server.py on the rest of standard input."""

import base64
import os
import runpy
import shlex
import sys
from pathlib import Path

script = base64.b64decode(sys.stdin.readline().strip()).decode("utf-8")
with open(sys.argv[1], "a", encoding="utf-8") as record:
    record.write(script + "\n---\n")
for line in script.splitlines():
    words = shlex.split(line)
    if words and words[0] == "export":
        name, _, value = words[1].partition("=")
        os.environ[name] = value
runpy.run_path(str(Path(__file__).with_name("mcp_test_server.py")), run_name="__main__")
