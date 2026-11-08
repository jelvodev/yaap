"""Write the OpenAPI description to a file, for generating the frontend's TypeScript types.

Usage: python -m app.scripts.export_openapi ../frontend/openapi.json
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def export_openapi(target: Path) -> None:
    schema = create_app().openapi()
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export_openapi(Path(sys.argv[1]))
