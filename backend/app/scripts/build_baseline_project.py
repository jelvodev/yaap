"""Build the baseline eval project as a git repository.

    python -m app.scripts.build_baseline_project <folder> [--mcp-folder <folder>] [--set savings]

<folder> is new, empty, or the folder of a new, empty YAAP project (data/workspace/<environment>/<project>). The
commits are always the same, so the eval cases' start commits are found. --mcp-folder also copies the issue tracker
MCP server one case uses, for example to data/workspace/<environment>/baseline-mcp, which the environment's runner
sees as /workspace/baseline-mcp. --set savings builds the context savings set (evals/savings/, the Freight project)
instead. See docs/usage.md, *The baseline eval set*.
"""

import argparse
import sys
from pathlib import Path

from app.evals.baseline import BASELINE_DIR, SAVINGS_DIR, BaselineError, build_project, copy_mcp_server

SETS = {"baseline": BASELINE_DIR, "savings": SAVINGS_DIR}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the baseline eval project (Stockroom) as a git repository.")
    parser.add_argument("folder", type=Path, help="a new or empty folder, or a new YAAP project's folder")
    parser.add_argument("--mcp-folder", type=Path, help="also copy the issue tracker MCP server here")
    parser.add_argument("--set", choices=sorted(SETS), default="baseline", help="which eval set to build")
    arguments = parser.parse_args()
    try:
        commits = build_project(arguments.folder, SETS[arguments.set])
    except BaselineError as error:
        print(f"Not built: {error}", file=sys.stderr)
        return 1
    print(f"Built the {arguments.set} project in {arguments.folder}.")
    print(f"  start commit:    {commits.start}")
    for commit in commits.history:
        print(f"  history commit:  {commit}")
    print(f"  eval cases:      {commits.cases}")
    if arguments.mcp_folder:
        for path in copy_mcp_server(arguments.mcp_folder):
            print(f"Copied {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
