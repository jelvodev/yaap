"""Measure YAAP's token footprint without a model.

    python -m app.scripts.token_footprint [--json <file>] [--python <interpreter>]

Prints a table per situation (a plain chat, a project conversation in the baseline project with its first tool
results, a goal turn): every system part, the tool definitions per origin, the user message and the tool results,
in characters and estimated tokens. --json also writes the full measurement, with the size of every tool. Run it
from backend/ in a checkout of the repository; a change that should send less puts its numbers before and after in
its commit message.
"""

import argparse
import json
import sys
from pathlib import Path

from app.evals.footprint import render_table, to_json
from app.evals.footprint_run import measure_footprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure what YAAP sends to a model, without a model.")
    parser.add_argument("--json", type=Path, help="also write the measurement as JSON to this file")
    parser.add_argument("--python", default=sys.executable, help="the interpreter for the stand-in MCP servers")
    arguments = parser.parse_args()
    footprint = measure_footprint(arguments.python)
    if arguments.json:
        arguments.json.write_text(json.dumps(to_json(footprint), indent=2) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.write(render_table(footprint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
