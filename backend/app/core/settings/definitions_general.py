"""The first part of the setting definitions, in registry order."""

from app.agent.workers import CLI_WORKERS, cli_worker_validator
from app.agent.workers import default_value as cli_default_value
from app.core.context_control import DEFAULT_COMPACT_AT, DEFAULT_TRIM_AT, WINDOW, validate_context_limit, validate_share
from app.core.prompts import ENGLISH
from app.core.settings.setting_types import SettingDefinition, SettingOption
from app.core.settings.validators import (
    INTERFACE_DEFAULT,
    validate_amount,
    validate_author_email,
    validate_author_text,
    validate_display_name,
    validate_interface,
    validate_model,
    validate_reply_language,
    validate_timezone,
    validate_work_language,
)
from app.core.utility_tasks import OFF_SETTING_KEY, ROUTABLE_TASKS, check_task_models, check_tasks_off
from app.core.utility_tasks import SETTING_KEY as UTILITY_TASKS_KEY
from app.providers.catalog import default_model


GENERAL_DEFINITIONS: tuple[SettingDefinition, ...] = (
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
        key="context_savings",
        label="Context savings",
        description=(
            "Whether YAAP sends less of what the model already has: a repeated read of an unchanged file "
            "becomes one line, reads of files that changed since are left out when old tool output is "
            "shortened, long test output keeps its failures and summary, and the repository map stays the same "
            "until the conversation is shortened or summarised. What is left out says how to read it again."
        ),
        levels=frozenset({"global", "environment"}),
        default="off",
        options=(SettingOption("off", "Off"), SettingOption("on", "On")),
    ),
    SettingDefinition(
        key="build_worker",
        label="Build steps",
        description=(
            "Who takes a step of an approved plan first when the step has a check YAAP can run. Worker: the "
            "preset's worker model, which gets the failure once more and then hands over to the main model. "
            "Main: the main model. A step without such a check always goes to the main model."
        ),
        levels=frozenset({"global", "environment", "project"}),
        default="worker",
        options=(SettingOption("worker", "Worker"), SettingOption("main", "Main")),
    ),
    SettingDefinition(
        key="utility_model",
        label="Utility model",
        description=(
            "A cheap model for small tasks: conversation titles, commit message proposals and front matter on "
            "import. It runs at low effort; its usage is shown apart from the main work."
        ),
        levels=frozenset({"global", "environment"}),
        default=default_model("utility"),
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
    SettingDefinition(
        key=OFF_SETTING_KEY,
        label="Background tasks switched off",
        description=(
            "Background tasks that do not run, such as titles or memory suggestions. A task that you start "
            "yourself and the check of outside content always run."
        ),
        levels=frozenset({"global", "environment"}),
        default=[],
        validate=check_tasks_off,
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
        key="search_model_download",
        label="Download the search model by itself",
        description=(
            "Download the search model (about 2.3 GB) in the background once the first start is finished, so "
            "search by meaning works. Until it is there, search finds words only. You can also download or remove "
            "it by hand in Settings › System › Models."
        ),
        levels=frozenset({"global"}),
        default="on",
        options=(SettingOption("on", "On"), SettingOption("off", "Off")),
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
)
