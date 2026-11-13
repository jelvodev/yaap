"""Validators for setting values."""

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.providers.registry import ProviderRegistry


INTERFACE_DEFAULT = {"theme": "dark", "utility_messages": False, "verbose_tools": False}


def validate_interface(value: Any, _providers: ProviderRegistry) -> str | None:
    """Interface preferences: the theme and two switches for what the chat shows."""
    if not isinstance(value, dict) or not set(value) <= set(INTERFACE_DEFAULT):
        return f"Use an object with only these keys: {', '.join(INTERFACE_DEFAULT)}."
    if "theme" in value and value["theme"] not in ("dark", "light"):
        return "The theme is dark or light."
    for key in ("utility_messages", "verbose_tools"):
        if key in value and not isinstance(value[key], bool):
            return f"{key} is true or false."
    return None


def validate_skill_names(value: Any, _providers: ProviderRegistry) -> str | None:
    if not isinstance(value, list) or len(value) > 20:
        return "Use a list of at most 20 skill names."
    if not all(isinstance(name, str) and 0 < len(name.strip()) <= 80 for name in value):
        return "Each skill name is text of at most 80 characters."
    return None


def validate_display_name(value: Any, _providers: ProviderRegistry) -> str | None:
    if not isinstance(value, str) or len(value.strip()) > 60:
        return "The display name must be text of at most 60 characters."
    return None


def validate_timezone(value: Any, _providers: ProviderRegistry) -> str | None:
    if value in ("", None):
        return None
    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError):
        return "Use a time zone name such as Europe/Amsterdam."
    return None


LANGUAGE_NAME = re.compile(r"[^\W\d_][\w ()'-]*")


def validate_reply_language(value: Any, _providers: ProviderRegistry) -> str | None:
    if value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) > 40 or not LANGUAGE_NAME.fullmatch(value.strip()):
        return "Name a language, for example Dutch, in at most 40 characters; leave it empty to follow your message."
    return None


def validate_work_language(value: Any, providers: ProviderRegistry) -> str | None:
    if value == "" or validate_reply_language(value, providers):
        return "Name a language, for example English, in at most 40 characters."
    return None


def validate_author_text(value: Any, _providers: ProviderRegistry) -> str | None:
    if not isinstance(value, str) or len(value) > 100 or "\n" in value:
        return "Use a single line of at most 100 characters."
    return None


def validate_author_email(value: Any, providers: ProviderRegistry) -> str | None:
    if value == "":
        return None
    if validate_author_text(value, providers) or not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+", value):
        return "Use an email address, for example you@example.com."
    return None


def validate_amount(value: Any) -> str | None:
    if value == "":
        return None
    try:
        amount = float(str(value).replace(",", ".").replace("$", "").strip())
    except ValueError:
        amount = -1.0
    if not isinstance(value, str) or not 0 <= amount <= 1_000_000:
        return "Give an amount in US dollars, such as 25, or leave it empty for no budget."
    return None


def validate_model(value: Any, providers: ProviderRegistry) -> str | None:
    if not isinstance(value, str) or providers.find_model(value) is None:
        return "Unknown model."
    return None
