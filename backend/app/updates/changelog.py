"""Reading one version's section out of CHANGELOG.md. Pure."""

import re


def changelog_section(text: str, version: str) -> str | None:
    """The body of `## <version> (<date>)`, without its heading and surrounding blank lines; None when absent."""
    lines = text.splitlines()
    heading = re.compile(rf"^## {re.escape(version)} \(")
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()
