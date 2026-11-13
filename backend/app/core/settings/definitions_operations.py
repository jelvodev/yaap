"""The second part of the setting definitions, in registry order."""

import re

from app.a2a.config import validate_agents
from app.agent.policy import check_commands, check_domains, check_tool_permissions
from app.backups.destinations import validate_destinations
from app.backups.runs import SCHEDULE_DEFAULT, validate_schedule
from app.board.settings import (
    BOARD_PARALLEL_BUILDS_KEY,
    BOARD_PUSH_KEY,
    BOARD_REPOSITORY_URL_KEY,
    BOARD_STORAGE_KEY,
    PARALLEL_BUILD_CHOICES,
    PROJECT_BOARD_KEY,
    validate_parallel_builds,
    validate_repository_url,
    validate_switch,
)
from app.core.model_policy import check_policy
from app.core.settings.setting_types import SettingDefinition, SettingOption, connection_setting_key
from app.core.settings.validators import validate_skill_names
from app.mcp_client.config import validate_servers
from app.providers.endpoints import SETTING_KEY as ENDPOINTS_KEY
from app.providers.endpoints import check_endpoints
from app.targets.folders import validate_target_setting
from app.tracing.otlp_settings import CONTENT_KEY as OTLP_CONTENT
from app.tracing.otlp_settings import ENDPOINT_KEY as OTLP_ENDPOINT
from app.tracing.otlp_settings import HEADERS_KEY as OTLP_HEADERS
from app.tracing.otlp_settings import validate_endpoint as validate_otlp_endpoint
from app.tracing.otlp_settings import validate_headers as validate_otlp_headers
from app.updates.schedule import DEFAULT_SCHEDULE as UPDATE_SCHEDULE_DEFAULT
from app.updates.schedule import schedule_error
from app.updates.source import DEFAULT_SOURCE, source_error


OPERATIONS_DEFINITIONS: tuple[SettingDefinition, ...] = (
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
        key=PROJECT_BOARD_KEY,
        label="Project board",
        description=(
            "Whether a project opens its board: cards in columns from Inbox to Done, kept on the project's "
            "yaap-board branch. Off: clicking the project only narrows the conversations, as before; the "
            "board's files are kept."
        ),
        levels=frozenset({"global", "environment", "project"}),
        default=True,
        validate=validate_switch,
    ),
    SettingDefinition(
        key=BOARD_PUSH_KEY,
        label="Push the board",
        description=(
            "Offer Push for the project's board. Pushing publishes every card, specification and plan on the "
            "yaap-board branch; on a public repository they become public. Nothing is pushed by itself."
        ),
        levels=frozenset({"project"}),
        default=False,
        validate=validate_switch,
    ),
    SettingDefinition(
        key=BOARD_STORAGE_KEY,
        label="Where the board is kept",
        description=(
            "The yaap-board branch of the project's own repository, or a separate private repository "
            "(board_repository_url). A pushed branch of a public repository is public too. Chosen when the board "
            "starts; a started board stays where it is."
        ),
        levels=frozenset({"project"}),
        default="branch",
        options=(SettingOption("branch", "Branch"), SettingOption("repository", "Separate repository")),
    ),
    SettingDefinition(
        key=BOARD_REPOSITORY_URL_KEY,
        label="Board repository",
        description=(
            "The https URL of the separate private repository that holds the board, without a user name or "
            "token; not the project's own remote. The GitHub token of the project is used to reach it."
        ),
        levels=frozenset({"project"}),
        default="",
        validate=validate_repository_url,
    ),
    SettingDefinition(
        key=BOARD_PARALLEL_BUILDS_KEY,
        label="Builds at a time",
        description=(
            "How many approved plans of the project build at the same time. Plans that name the same files never "
            "build together; the others wait in a queue and start when a build ends."
        ),
        levels=frozenset({"project"}),
        default="2",
        options=tuple(SettingOption(count, count) for count in PARALLEL_BUILD_CHOICES),
        validate=validate_parallel_builds,
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
        key="update_check_schedule",
        label="Update check",
        description=(
            "When YAAP asks GitHub whether a newer version exists (cron, in the global time zone, at most once an "
            "hour), or off. It also asks a few seconds after start; off stops both."
        ),
        levels=frozenset({"global"}),
        default=UPDATE_SCHEDULE_DEFAULT,
        validate=lambda value, _providers: schedule_error(value),
    ),
    SettingDefinition(
        key="update_source",
        label="Update source",
        description="The GitHub repository the update check reads releases from, as owner/repository.",
        levels=frozenset({"global"}),
        default=DEFAULT_SOURCE,
        validate=lambda value, _providers: source_error(value),
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
