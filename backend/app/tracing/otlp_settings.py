"""The settings of the OpenTelemetry export: where traces go and what they hold.

- `otlp_endpoint`: the base address of an OTLP/HTTP collector; YAAP posts JSON to `<endpoint>/v1/traces` after each
  reply. Empty: nothing is sent (the downloads still work).
- `otlp_headers`: headers sent with each export, by name. A value names its secret as a `{{secret:NAME}}` placeholder
  from the vault (text around it is allowed, as in `Bearer {{secret:OTLP_TOKEN}}`); the value itself is never stored
  in the setting.
- `otlp_include_content`: whether the export (sent and downloaded) holds the prompt content from the context
  snapshots and the arguments of tool calls. Off by default.

The validators are pure functions; the settings themselves are defined in `core/settings.py`.
"""

import re
from typing import Any

from app.secrets.service import PLACEHOLDER

ENDPOINT_KEY = "otlp_endpoint"
HEADERS_KEY = "otlp_headers"
CONTENT_KEY = "otlp_include_content"
SETTING_KEYS = (ENDPOINT_KEY, HEADERS_KEY, CONTENT_KEY)

TRACES_PATH = "/v1/traces"
MAX_HEADERS = 10
MAX_HEADER_VALUE = 500
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
ENDPOINT = re.compile(r"^https?://[^\s/?#]+(/[^\s?#]*)?$")
# Headers YAAP sets itself, or that describe the connection rather than the export.
RESERVED_HEADERS = frozenset({"content-type", "content-length", "content-encoding", "host", "transfer-encoding"})


def validate_endpoint(value: Any, _providers: object = None) -> str | None:
    if value == "":
        return None
    if not isinstance(value, str) or len(value) > 300 or not ENDPOINT.fullmatch(value):
        return "Use the collector's http(s) address without a query, such as http://otel-collector:4318."
    return None


def validate_headers(value: Any, _providers: object = None) -> str | None:
    if not isinstance(value, dict):
        return "Use an object with a header per name."
    if len(value) > MAX_HEADERS:
        return f"At most {MAX_HEADERS} headers."
    seen: set[str] = set()
    for name, header in value.items():
        if not HEADER_NAME.fullmatch(str(name)):
            return f"Header {name!r}: use letters, digits and -, at most 64 characters."
        if str(name).lower() in RESERVED_HEADERS:
            return f"Header {name}: YAAP sets this header itself."
        if str(name).lower() in seen:
            return f"Header {name} is listed twice."
        seen.add(str(name).lower())
        if not isinstance(header, str) or not header.strip() or len(header) > MAX_HEADER_VALUE or "\n" in header:
            return f"Header {name}: use a single line of at most {MAX_HEADER_VALUE} characters."
        if PLACEHOLDER.search(header) is None:
            return (
                f"Header {name}: name the secret as a placeholder such as {{{{secret:OTLP_TOKEN}}}} (for example "
                "Bearer {{secret:OTLP_TOKEN}}), never the value itself."
            )
    return None


def traces_url(endpoint: str) -> str:
    """Where the export is posted: the endpoint with /v1/traces, as OTLP/HTTP exporters do with a base address."""
    base = endpoint.strip().rstrip("/")
    return base if base.endswith(TRACES_PATH) else base + TRACES_PATH


def stored_headers(value: Any) -> dict[str, str]:
    """The headers of a stored setting value; nothing when it is not valid."""
    if not isinstance(value, dict) or validate_headers(value):
        return {}
    return {str(name): str(header).strip() for name, header in value.items()}
