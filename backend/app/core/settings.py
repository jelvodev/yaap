"""Settings with inheritance: project → environment → global → application default."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.a2a.config import validate_agents
from app.agent.policy import ToolPolicy, check_commands, check_domains, check_tool_permissions
from app.agent.workers import CLI_WORKERS, cli_worker_validator
from app.agent.workers import default_value as cli_default_value
from app.backups.destinations import validate_destinations
from app.backups.runs import SCHEDULE_DEFAULT, validate_schedule
from app.core.context_control import (
    DEFAULT_COMPACT_AT,
    DEFAULT_TRIM_AT,
    WINDOW,
    validate_context_limit,
    validate_share,
)
from app.core.model_policy import check_policy
from app.core.prompts import ENGLISH, Languages
from app.core.utility_tasks import ROUTABLE_TASKS, check_task_models
from app.core.utility_tasks import SETTING_KEY as UTILITY_TASKS_KEY
from app.mcp_client.config import validate_servers
from app.providers.endpoints import SETTING_KEY as ENDPOINTS_KEY
from app.providers.endpoints import check_endpoints
from app.providers.registry import ProviderRegistry
from app.storage.models import Setting
from app.targets.folders import validate_target_setting
from app.tracing.otlp_settings import CONTENT_KEY as OTLP_CONTENT
from app.tracing.otlp_settings import ENDPOINT_KEY as OTLP_ENDPOINT
from app.tracing.otlp_settings import HEADERS_KEY as OTLP_HEADERS
from app.tracing.otlp_settings import validate_endpoint as validate_otlp_endpoint
from app.tracing.otlp_settings import validate_headers as validate_otlp_headers

Level = Literal["global", "environment", "project"]
Source = Literal["default", "global", "environment", "project"]
Validator = Callable[[Any, ProviderRegistry], str | None]


@dataclass(frozen=True)
class SettingOption:
    value: str
    label: str


def no_extra_validation(_value: Any, _providers: ProviderRegistry) -> str | None:
    return None


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    description: str
    levels: frozenset[str]
    default: Any
    validate: Validator = no_extra_validation
    # Fixed choices. When set, a value must be one of them; for a merged dictionary, the keys it can hold.
    options: tuple[SettingOption, ...] = ()
    # A dictionary merged key by key over the levels, instead of the nearest level replacing the whole value.
    merge: bool = False

    def check(self, value: Any, providers: ProviderRegistry) -> str | None:
        if self.options and not self.merge and value not in {option.value for option in self.options}:
            return f"Choose one of: {', '.join(option.label for option in self.options)}."
        return self.validate(value, providers)


@dataclass(frozen=True)
class ResolvedSetting:
    definition: SettingDefinition
    value: Any
    source: Source
    inherited_value: Any
    inherited_source: Source
    # What is stored at the requested level itself, or None when that level inherits.
    level_value: Any = None


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


def resolve_tool_policy(session: Session, environment_id: int, project_id: int | None) -> ToolPolicy:
    """What the agent may do without asking in this environment or project."""

    def value(key: str) -> object:
        return resolve_setting(session, SETTING_DEFINITIONS[key], environment_id, project_id).value

    commands = value("command_allowlist")
    domains = value("web_allowlist")
    return ToolPolicy(
        permissions=dict(value("tool_permissions") or {}),  # type: ignore[call-overload]
        commands=tuple(commands) if isinstance(commands, list) else (),
        domains=tuple(domain.lower().strip() for domain in domains) if isinstance(domains, list) else (),
    )


def resolve_text(session: Session, key: str, environment_id: int | None) -> str:
    return str(resolve_setting(session, SETTING_DEFINITIONS[key], environment_id).value or "")


def resolve_languages(session: Session, environment_id: int | None, project_id: int | None = None) -> Languages:
    """The reply and work language at the nearest level."""
    reply = resolve_setting(session, SETTING_DEFINITIONS["reply_language"], environment_id, project_id).value
    work = resolve_setting(session, SETTING_DEFINITIONS["work_language"], environment_id, project_id).value
    return Languages(reply=str(reply or "").strip(), work=str(work or "").strip() or ENGLISH)


def connection_setting_key(provider: str) -> str:
    return f"{provider}_connection"


def validate_model(value: Any, providers: ProviderRegistry) -> str | None:
    if not isinstance(value, str) or providers.find_model(value) is None:
        return "Unknown model."
    return None


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    definition.key: definition
    for definition in (
        SettingDefinition(
            key="display_name",
            label="Display name",
            description="Your name, shown at the global level and in greetings.",
            levels=frozenset({"global"}),
            default="",
            validate=validate_display_name,
        ),
        SettingDefinition(
            key="timezone",
            label="Time zone",
            description=(
                "The time zone of the date and time the model gets with each message. Taken from your browser "
                "until you set it."
            ),
            levels=frozenset({"global"}),
            default="",
            validate=validate_timezone,
        ),
        SettingDefinition(
            key="reply_language",
            label="Reply language",
            description=(
                "The language YAAP replies in, also for conversation titles. Empty: the language of your message."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default="",
            validate=validate_reply_language,
        ),
        SettingDefinition(
            key="work_language",
            label="Work language",
            description=(
                "The language of code comments, documentation, commit messages, pull requests, project memory and "
                "rules, whatever language the conversation is in."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default=ENGLISH,
            validate=validate_work_language,
        ),
        SettingDefinition(
            key="context_limit",
            label="Context limit",
            description=(
                "How large a conversation's context may grow, in tokens. Old tool output is shortened and the "
                "conversation summarised against this limit instead of the model's window, which keeps long "
                "conversations cheaper. The model keeps its full window, so a reply never fails on it. "
                f'"{WINDOW}": the model\'s window. A conversation can choose its own.'
            ),
            levels=frozenset({"global", "environment", "project"}),
            default=WINDOW,
            validate=validate_context_limit,
        ),
        SettingDefinition(
            key="auto_compact",
            label="Compact automatically",
            description=(
                "Whether YAAP keeps long conversations within the context limit by itself: it shortens old tool "
                "output, then summarises the earlier messages. Off: only /compact and Summarise now do. Claude Code "
                "and Codex compact their own sessions either way."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default="on",
            options=(SettingOption("on", "On"), SettingOption("off", "Off")),
        ),
        SettingDefinition(
            key="compact_at",
            label="Summarise at",
            description="The share of the context limit, in percent, at which YAAP summarises the earlier messages.",
            levels=frozenset({"global", "environment", "project"}),
            default=DEFAULT_COMPACT_AT,
            validate=validate_share,
        ),
        SettingDefinition(
            key="trim_at",
            label="Shorten old tool output at",
            description=(
                "The share of the context limit, in percent, at which YAAP shortens the long tool output of older "
                "turns, a cheaper step before a summary."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default=DEFAULT_TRIM_AT,
            validate=validate_share,
        ),
        SettingDefinition(
            key="repo_map",
            label="Repository map",
            description=(
                "Whether a project conversation starts with a map of the project's folders, main modules and public "
                "symbols, about 1,500 tokens, made once per commit. Off: the agent looks around with its tools."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default="on",
            options=(SettingOption("on", "On"), SettingOption("off", "Off")),
        ),
        SettingDefinition(
            key="utility_model",
            label="Utility model",
            description=(
                "A cheap model for small tasks: conversation titles, commit message proposals and front matter on "
                "import. It runs at low effort; its usage is shown apart from the main work."
            ),
            levels=frozenset({"global", "environment"}),
            default="claude-haiku-5-5",
            validate=validate_model,
        ),
        SettingDefinition(
            key="plain_chat_preset",
            label="Preset for chats outside a project",
            description=(
                "The preset a new conversation outside a project starts with, by name. Empty: the default preset. "
                "Project conversations start with the project's preset, else the default preset."
            ),
            levels=frozenset({"environment"}),
            default="",
            validate=lambda value, _providers: (
                None if isinstance(value, str) and len(value.strip()) <= 40 else "Name a preset, or leave it empty."
            ),
        ),
        SettingDefinition(
            key=UTILITY_TASKS_KEY,
            label="Utility model per task",
            description=(
                "A model of its own for a small task, such as a local model for titles and commit messages. A task "
                "without one uses the utility model. The check of outside content always uses the utility model."
            ),
            levels=frozenset({"global", "environment"}),
            default={},
            validate=check_task_models,
            options=tuple(SettingOption(task, label) for task, label in ROUTABLE_TASKS.items()),
            merge=True,
        ),
        # Coding agent CLIs as workers for delegate.
        *(
            SettingDefinition(
                key=worker.setting,
                label=f"{worker.label} as worker",
                description=(
                    f"The model and effort of {worker.label} when a delegated task names it as worker "
                    f"({worker.name}). It runs on your subscription, with only YAAP's tools."
                ),
                levels=frozenset({"global", "environment"}),
                default=cli_default_value(worker),
                validate=cli_worker_validator(worker.provider),
            )
            for worker in CLI_WORKERS.values()
        ),
        SettingDefinition(
            key="embedding_model",
            label="Embedding model",
            description=(
                "The model that turns conversations, project memory and Blueprint files into vectors for semantic "
                "search. It runs inside YAAP, so no text leaves the machine and it costs nothing. Off: search is "
                "full-text only. A change rebuilds the index in the background."
            ),
            levels=frozenset({"global"}),
            default="bge-m3",
            options=(
                SettingOption("bge-m3", "bge-m3 (local, finds across Dutch and English)"),
                SettingOption("off", "Off: full-text search only"),
            ),
        ),
        SettingDefinition(
            key="git_author_name",
            label="Git author name",
            description="The name on commits you make in YAAP. Empty: commits are made as YAAP.",
            levels=frozenset({"global", "environment"}),
            default="",
            validate=validate_author_text,
        ),
        SettingDefinition(
            key="git_author_email",
            label="Git author email",
            description="The email on commits you make in YAAP; GitHub links commits to your account by this address.",
            levels=frozenset({"global", "environment"}),
            default="",
            validate=validate_author_email,
        ),
        SettingDefinition(
            key="budget_total",
            label="Monthly budget, all environments (USD)",
            description=(
                "The most that API-key use may cost per calendar month across all environments. Empty: no limit. "
                "Replies through your Claude subscription do not count."
            ),
            levels=frozenset({"global"}),
            default="",
            validate=lambda value, _providers: validate_amount(value),
        ),
        SettingDefinition(
            key="budget_environment",
            label="Monthly budget for this environment (USD)",
            description=(
                "The most that API-key use in this environment may cost per calendar month. Empty: no own limit. "
                "When the budget for all environments is reached first, that one applies."
            ),
            levels=frozenset({"environment"}),
            default="",
            validate=lambda value, _providers: validate_amount(value),
        ),
        SettingDefinition(
            key="interface",
            label="Interface preferences",
            description=(
                "The theme, whether utility calls show in the chat, and whether tool calls open with their full "
                "input and output. Set from the Preferences menu in the sidebar."
            ),
            levels=frozenset({"global"}),
            default=INTERFACE_DEFAULT,
            validate=validate_interface,
            merge=True,
        ),
        SettingDefinition(
            key="default_skills",
            label="Default skills",
            description=(
                "Skills pinned in every new conversation from the first message, for example an answer style. "
                "One name per line. Removing a skill in a conversation applies to that conversation only."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default=[],
            validate=validate_skill_names,
        ),
        SettingDefinition(
            key="tool_permissions",
            label="Tool permissions",
            description=(
                "Per tool: run it automatically or ask you first. Set per tool and inherited per tool, so a project "
                "can make one tool stricter without repeating the others. Pushing always asks."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default={},
            validate=lambda value, _providers: check_tool_permissions(value),
            merge=True,
        ),
        SettingDefinition(
            key="command_allowlist",
            label="Commands that run without asking",
            description=(
                "One command per line, such as 'npm test' or 'pytest'. A command runs without asking when it is one "
                "of these, optionally with extra arguments, and contains no shell syntax such as ; | && or $."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default=[],
            validate=lambda value, _providers: check_commands(value),
        ),
        SettingDefinition(
            key="injection_check",
            label="Check approvals after outside content",
            description=(
                "After an agent has read outside content (a web page, an attachment, a skill from GitHub), every "
                "action asks first. When this is on, the utility model also checks each of those requests for signs "
                "that it follows instructions from that content and marks suspicious ones. It costs a small utility "
                "call per request; you still decide."
            ),
            levels=frozenset({"global", "environment", "project"}),
            default="off",
            options=(
                SettingOption("off", "Off"),
                SettingOption("on", "Check with the utility model"),
            ),
        ),
        SettingDefinition(
            key="web_allowlist",
            label="Websites read without asking",
            description="One domain per line, such as docs.python.org. Its subdomains are included.",
            levels=frozenset({"global", "environment", "project"}),
            default=[],
            validate=lambda value, _providers: check_domains(value),
        ),
        SettingDefinition(
            key="knowledge_folder",
            label="Knowledge folder",
            description=(
                "A folder of the project, such as docs, whose Markdown, text and PDF files are knowledge without "
                "uploading them. Changed files are read again; removed files leave the knowledge. Empty: none."
            ),
            levels=frozenset({"project"}),
            default="",
            validate=lambda value, _providers: None
            if isinstance(value, str)
            and len(value) <= 200
            and not value.strip().startswith(("/", "\\"))
            and ".." not in value.replace("\\", "/").split("/")
            and ":" not in value
            else "Use a folder inside the project, such as docs.",
        ),
        SettingDefinition(
            key="target",
            label="Where the agent works",
            description=(
                "YAAP's container ({}), a computer from Settings › Targets with the project's folder on it, or the "
                "project's own sandbox container ({\"sandbox\": true, \"network\": false}). Conversations started "
                "after a change use it."
            ),
            levels=frozenset({"project"}),
            default={},
            validate=validate_target_setting,
        ),
        SettingDefinition(
            key="runner_network",
            label="Runner network",
            description=(
                "Whether the environment's runner, where the commands of its projects run, can reach the internet "
                "(installing packages needs it). YAAP's own API is never reachable from it. A change makes the runner "
                "again on its next use."
            ),
            levels=frozenset({"global", "environment"}),
            default="on",
            options=(SettingOption("on", "Online"), SettingOption("off", "Offline")),
        ),
        SettingDefinition(
            key="eval_parallel_cases",
            label="Eval cases at a time",
            description=(
                "How many eval cases play at the same time in this environment, over all its eval runs. Each case "
                "is a reply of its own in the environment's runner."
            ),
            levels=frozenset({"global", "environment"}),
            default="1",
            options=tuple(SettingOption(str(count), str(count)) for count in (1, 2, 3, 4)),
        ),
        SettingDefinition(
            key="backup_schedule",
            label="Backup schedule",
            description="Whether YAAP makes a backup on a schedule (cron, in the global time zone), and how many it keeps.",
            levels=frozenset({"global"}),
            default=SCHEDULE_DEFAULT,
            validate=validate_schedule,
        ),
        SettingDefinition(
            key="backup_destinations",
            label="Backup destinations",
            description="Folders and network shares each backup is copied to, besides data/backups.",
            levels=frozenset({"global"}),
            default=[],
            validate=validate_destinations,
        ),
        SettingDefinition(
            key="ollama_url",
            label="Ollama address",
            description=(
                "Where YAAP reaches Ollama for local models, such as http://host.docker.internal:11434. Its pulled "
                "models then show in the model list, without cost. Empty switches Ollama off."
            ),
            levels=frozenset({"global"}),
            default="",
            validate=lambda value, _providers: None
            if value == "" or (isinstance(value, str) and re.match(r"^https?://[^\s]+$", value))
            else "Use an http(s) address, such as http://host.docker.internal:11434.",
        ),
        SettingDefinition(
            key=ENDPOINTS_KEY,
            label="OpenAI-compatible endpoints",
            description=(
                "Servers that speak the OpenAI Chat Completions API, such as OpenRouter, LM Studio or vLLM: a list of "
                "{name, base_url, local}. Each is a provider named like the endpoint, with the models it lists as "
                "<name>/<model>. Set local for a server on your own computer or network, so 'only local models' "
                "allows it."
            ),
            levels=frozenset({"global"}),
            default=[],
            validate=check_endpoints,
        ),
        SettingDefinition(
            key="mcp_servers",
            label="MCP servers",
            description=(
                "Outside MCP servers whose tools the agent may use, by name. A project adds servers or overrides one "
                "by name (null switches an inherited server off). Their tools ask before each call unless listed in "
                "auto_tools; what they return counts as outside content. A server with more than five tools offers "
                "them through find_tools unless list_all_tools is true."
            ),
            levels=frozenset({"environment", "project"}),
            default={},
            validate=validate_servers,
            merge=True,
        ),
        SettingDefinition(
            key="a2a_agents",
            label="A2A agents",
            description=(
                "Other AI agents the agent may ask over the A2A protocol, by name: url, an optional token as "
                "{{secret:NAME}}, a description, timeout and enabled. Each call asks first; the answer counts as "
                "outside content."
            ),
            levels=frozenset({"environment"}),
            default={},
            validate=validate_agents,
        ),
        SettingDefinition(
            key="model_policy",
            label="Model policy",
            description=(
                "Which connections and models may be used (a list, or only local models), whether a model must be "
                "marked for business data processing, which A2A agents may be called, and patterns that block a "
                "request before it leaves YAAP. A project can only narrow its environment's policy."
            ),
            levels=frozenset({"environment", "project"}),
            default={},
            validate=check_policy,
        ),
        SettingDefinition(
            key=OTLP_ENDPOINT,
            label="OpenTelemetry endpoint",
            description=(
                "An OTLP/HTTP collector, such as http://otel-collector:4318. After each reply YAAP sends its trace "
                "there as JSON (to /v1/traces), in the background. Empty: nothing is sent."
            ),
            levels=frozenset({"global", "environment"}),
            default="",
            validate=validate_otlp_endpoint,
        ),
        SettingDefinition(
            key=OTLP_HEADERS,
            label="OpenTelemetry headers",
            description=(
                "Headers sent with each export, by name. A value names its secret from the vault as "
                "{{secret:NAME}}, such as Bearer {{secret:OTLP_TOKEN}}; the value is filled in when sending."
            ),
            levels=frozenset({"global", "environment"}),
            default={},
            validate=validate_otlp_headers,
        ),
        SettingDefinition(
            key=OTLP_CONTENT,
            label="Include prompt content",
            description=(
                "Whether exported traces, sent and downloaded, hold what the model saw (system prompt and "
                "messages, with secrets as placeholders) and the arguments of tool calls. Off: only names, "
                "times, tokens and costs."
            ),
            levels=frozenset({"global", "environment"}),
            default="off",
            options=(SettingOption("off", "Off"), SettingOption("on", "Include")),
        ),
        SettingDefinition(
            key=connection_setting_key("anthropic"),
            label="Claude connection",
            description=(
                "How YAAP reaches Claude. Claude Code uses your Claude subscription through the Claude Code "
                "installed in YAAP, signed in once. An API key is billed per use in the Anthropic Console."
            ),
            levels=frozenset({"global", "environment"}),
            default="claude_code",
            options=(
                SettingOption("claude_code", "Claude Code (subscription)"),
                SettingOption("api_key", "Anthropic API key"),
            ),
        ),
        SettingDefinition(
            key=connection_setting_key("openai"),
            label="OpenAI connection",
            description=(
                "How YAAP reaches GPT models. An API key is billed per use on the OpenAI platform. Codex uses your "
                "ChatGPT subscription through the Codex CLI installed in YAAP, signed in once."
            ),
            levels=frozenset({"global", "environment"}),
            default="api_key",
            options=(
                SettingOption("api_key", "OpenAI API key"),
                SettingOption("codex", "Codex (ChatGPT subscription)"),
            ),
        ),
    )
}


def resolve_connection(session: Session, provider: str, environment_id: int) -> str | None:
    """The connection an environment uses for a provider, or None when the provider has only one."""
    definition = SETTING_DEFINITIONS.get(connection_setting_key(provider))
    if definition is None:
        return None
    return str(resolve_setting(session, definition, environment_id).value)


def find_setting_row(
    session: Session, key: str, environment_id: int | None, project_id: int | None = None
) -> Setting | None:
    environment_filter = Setting.environment_id.is_(None) if environment_id is None else Setting.environment_id == environment_id
    project_filter = Setting.project_id.is_(None) if project_id is None else Setting.project_id == project_id
    return session.scalar(select(Setting).where(Setting.key == key, environment_filter, project_filter))


def resolve_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, project_id: int | None = None
) -> ResolvedSetting:
    """The value at a level and where it comes from. The nearest level wins; merged settings combine all levels."""
    rows: list[tuple[Source, Any]] = []
    if "global" in definition.levels and (row := find_setting_row(session, definition.key, None)) is not None:
        rows.append(("global", row.value))
    if environment_id is not None and "environment" in definition.levels:
        if (row := find_setting_row(session, definition.key, environment_id)) is not None:
            rows.append(("environment", row.value))
    if environment_id is not None and project_id is not None and "project" in definition.levels:
        if (row := find_setting_row(session, definition.key, environment_id, project_id)) is not None:
            rows.append(("project", row.value))

    requested: Source = "project" if project_id is not None else "environment" if environment_id is not None else "global"
    above = [(source, value) for source, value in rows if source != requested]
    own = next((value for source, value in rows if source == requested), None)

    def combine(levels: list[tuple[Source, Any]]) -> tuple[Any, Source]:
        if not levels:
            return definition.default, "default"
        if definition.merge:
            merged = dict(definition.default or {})
            for _source, value in levels:
                merged.update(value or {})
            return merged, levels[-1][0]
        return levels[-1][1], levels[-1][0]

    inherited_value, inherited_source = combine(above)
    value, source = combine(rows)
    return ResolvedSetting(definition, value, source, inherited_value, inherited_source, own)


def store_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, value: Any, project_id: int | None = None
) -> None:
    row = find_setting_row(session, definition.key, environment_id, project_id)
    if row is None:
        row = Setting(key=definition.key, environment_id=environment_id, project_id=project_id)
        session.add(row)
    row.value = value
    row.updated_at = datetime.now(UTC)
    session.commit()


def clear_setting(
    session: Session, definition: SettingDefinition, environment_id: int | None, project_id: int | None = None
) -> None:
    row = find_setting_row(session, definition.key, environment_id, project_id)
    if row is not None:
        session.delete(row)
        session.commit()
