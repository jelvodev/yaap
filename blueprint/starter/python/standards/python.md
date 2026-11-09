---
sources: [wshobson/plugins/python-development/skills/python-type-safety, wshobson/plugins/python-development/skills/uv-package-manager, own]
category: python
summary: Python back ends are fully typed, locked with uv, tested with pytest, and keep each concern and each layer
  in one place.
rules:
- Annotate every function and keep the type checker clean in strict mode; types catch at review what tests miss.
- Manage dependencies with uv and a committed lock file, so every machine and CI run the same versions.
- Test with pytest, tests named after the behaviour, decision logic as parametrized tables that show missing cases.
- Keep one place per concern; search for an existing helper first, because two copies drift apart.
- Keep handlers thin (parse, call a service, map its errors), so logic can be reused and tested without HTTP.
---

# Python

How to write Python back ends. The rules above are always in the prompt; this text explains them.

## Types

Every function has annotated parameters and a return type, `-> None` included. Run the project's type checker (mypy
or pyright) in strict mode with the tests, and fix what it reports rather than silencing it.

- Use `X | None` and handle the `None` case where it arises.
- Status and kind fields are a `Literal` or an `Enum`, not a free string; the checker then finds a missed case.
- Prefer a `dataclass` or a Pydantic model to a `dict` that travels through several functions.
- A dependency is typed as a class or a `Protocol`, never `Any`.
- A suppression (`# type: ignore[code]`, `# noqa: CODE`) names the rule and the reason on the same line.

## Dependencies

uv manages the Python version, the virtual environment and the dependencies, all declared in `pyproject.toml`. The
lock file (`uv.lock`) is committed, so a fresh clone, a colleague and CI install exactly the same versions. Run tools
through it (`uv run pytest`, `uv run mypy`), so they use the project's environment and not whatever is on the path.

Add a dependency only when it earns its place, with the project's package manager, and say why in the commit. A
change to the lock file that nobody asked for is a finding in review.

## Tests

pytest, with tests under `tests/` mirroring the package. A test name says the behaviour
(`test_expired_token_is_refused`), so a failure reads as a sentence. Decision logic (permissions, prices, parsing)
gets a table with `pytest.mark.parametrize`: one row per case makes the missing case visible. Mock only at the edge
of the system; the `python-testing` skill has the method.

## One place per concern

Before writing a helper, search for one. Configuration is read once into a typed settings object; the environment
for subprocesses, the HTTP client, the clock and logging setup each have a single owner. A second copy of a list, a
check or a formatter will drift from the first, and the bug shows up in whichever copy was not updated.

## Layers

An HTTP handler parses the request, calls a service and turns its result or its domain error into a response. The
logic lives in the service, which knows nothing about HTTP, so a CLI, a scheduled job or a test can call the same
code. Database access sits behind the service too. The `fastapi-service` skill shows the layout.

## Errors and size

Catch the exceptions you expect, and catch broadly only at a boundary that must keep running, with the error logged.
A function over about 60 lines or a module over about 600 is split by responsibility before more is added, not into
a `utils` module.
