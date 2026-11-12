"""The utility model: small tasks such as titles and commit messages, on a cheap model.

Utility calls go through the environment's own connection, like the main work, and are recorded with purpose
"utility". They never block the user: when one fails, the caller keeps its fallback.
"""

import logging
from dataclasses import dataclass
from typing import Literal
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import record_request, utility_entry, with_refusal
from app.core.model_choice import ForUtility, ModelChoice, ModelChoiceError, PolicyForbids, read_key, resolve_provider
from app.core.outgoing_checks import OutgoingRules, checked_provider, outgoing_rules, request_refusal
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.core.utility_tasks import OFF_SETTING_KEY, task_enabled, task_label
from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialType
from app.providers.base import AgentRequest, Completed, HistoryMessage, ProviderError, SystemPart, TextDelta
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, UsageRecord

logger = logging.getLogger(__name__)

UTILITY_SYSTEM = (
    "You do small, precise tasks for YAAP, a personal AI development assistant. Reply with exactly what is asked, "
    "without introduction, quotes or explanation."
)
TITLE_WORDS = 6
MAX_DIFF_CHARS = 40_000
SWITCHED_OFF_REASON = "Switched off in Settings › Models › Background tasks."


@dataclass(frozen=True)
class UtilityContext:
    session_factory: sessionmaker[Session]
    cipher: CredentialCipher
    credential_types: dict[str, CredentialType]
    providers: ProviderRegistry


@dataclass(frozen=True)
class UtilitySkipped:
    """The model policy allows no model for a utility task, so it was not asked. reason is the policy's own text,
    which names the level that refuses and where to change it."""

    task: str | None
    reason: str

    @property
    def message(self) -> str:
        """One sentence for the user who asked for the task."""
        return f"No model was asked for {task_label(self.task).lower()}. {self.reason}"


async def run_utility(
    context: UtilityContext,
    environment_id: int,
    prompt: str,
    conversation_id: int | None = None,
    max_tokens: int = 1000,
    task: str | None = None,
    role: Literal["utility", "worker"] = "utility",
    project_id: int | None = None,
) -> str | None:
    """The utility model's answer, or None when it is not available or the model policy skipped the task. A caller
    that tells the user why uses run_utility_or_skip()."""
    answer = await run_utility_or_skip(
        context, environment_id, prompt, conversation_id, max_tokens, task, role, project_id
    )
    return None if isinstance(answer, UtilitySkipped) else answer


async def run_utility_or_skip(
    context: UtilityContext,
    environment_id: int,
    prompt: str,
    conversation_id: int | None = None,
    max_tokens: int = 1000,
    task: str | None = None,
    role: Literal["utility", "worker"] = "utility",
    project_id: int | None = None,
) -> str | UtilitySkipped | None:
    """The utility model's answer; UtilitySkipped when the model policy allows no model for the task or the user
    switched the task off (nothing is sent or recorded then); None when no model is available otherwise. With role
    worker, the preset's worker model answers (for larger tasks such as summaries), else the utility model.
    project_id names the project a task without a conversation works for (a commit message): its model policy
    applies, and the audit log records it. A conversation brings its own."""
    with context.session_factory() as session:
        conversation = session.get(Conversation, conversation_id) if conversation_id is not None else None
        preset = conversation.preset if conversation else None
        if conversation is not None:
            project_id = conversation.project_id
        off = resolve_setting(session, SETTING_DEFINITIONS[OFF_SETTING_KEY], environment_id).value
        if not task_enabled(task, off):
            logger.debug("The utility task %s is switched off.", task)
            return UtilitySkipped(task, SWITCHED_OFF_REASON)
        try:
            request = ForUtility(preset, role, project_id, task)
            choice = resolve_provider(session, context.providers, environment_id, request)
            api_key = read_key(session, context.cipher, context.credential_types, choice)
            outgoing = outgoing_rules(session, context.cipher, environment_id, project_id)
        except PolicyForbids as refusal:
            # No allowed utility model: the task is skipped and says so, and its caller keeps its fallback.
            logger.warning("The utility task %s was skipped: %s", task or "(unnamed)", refusal)
            refused = utility_entry(
                refusal.choice, utility_request(refusal.choice, prompt, max_tokens), task, project_id, conversation_id
            )
            record_request(context.session_factory, with_refusal(refused, str(refusal)))
            return UtilitySkipped(task, str(refusal))
        except ModelChoiceError:
            return None
    answer = await ask_utility_model(
        context, UtilityCall(choice, api_key, outgoing, task, project_id, conversation_id), prompt, max_tokens
    )
    return (answer.text or None) if answer is not None else None


@dataclass(frozen=True)
class UtilityCall:
    """A utility request whose model, key and outgoing rules are settled."""

    choice: ModelChoice
    api_key: str | None
    outgoing: OutgoingRules
    task: str | None = None
    project_id: int | None = None
    conversation_id: int | None = None


@dataclass(frozen=True)
class UtilityAnswer:
    text: str
    # The provider's report of the call (model, tokens, billing); None when the stream ended without one.
    completed: Completed | None
    # In USD; None on a subscription or for a model outside the catalog.
    cost: float | None


async def ask_utility_model(
    context: UtilityContext, call: UtilityCall, prompt: str, max_tokens: int
) -> UtilityAnswer | None:
    """Send one utility request, recorded in the audit log and the usage, and return the answer with its cost; None
    when the provider fails or the outgoing check blocks it."""
    request = utility_request(call.choice, prompt, max_tokens)
    entry = utility_entry(call.choice, request, call.task, call.project_id, call.conversation_id)
    record_request(context.session_factory, with_refusal(entry, request_refusal(request, call.outgoing)))
    provider = checked_provider(call.choice.spec.create(call.api_key), call.outgoing)
    parts: list[str] = []
    completed: Completed | None = None
    try:
        async for event in provider.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, Completed):
                completed = event
    except ProviderError as error:
        logger.info("The utility model was not available: %s", error.message)
        return None
    finally:
        try:
            await provider.close()
        except Exception:  # noqa: BLE001 - closing must not hide the answer
            logger.warning("Closing the utility provider failed.", exc_info=True)
    text = "".join(parts).strip()
    if completed is None:
        return UtilityAnswer(text, None, None)
    summary = clean_line(text, 200) if call.task else None
    environment_id = call.choice.environment_id
    provider_name = call.choice.spec.name
    record_utility_usage(context, environment_id, call.conversation_id, provider_name, completed, call.task, summary)
    return UtilityAnswer(text, completed, usage_cost(context.providers, completed))


def usage_cost(providers: ProviderRegistry, completed: Completed) -> float | None:
    """What a call cost in USD by the catalog's prices; None on a subscription or for a model outside the catalog."""
    served = providers.find_model(completed.served_by_model)
    return calculate_cost(served, completed.usage) if served and completed.billing == "metered" else None


def utility_request(choice: ModelChoice, prompt: str, max_tokens: int) -> AgentRequest:
    return AgentRequest(
        model=choice.model,
        effort=choice.effort,
        system=[SystemPart(UTILITY_SYSTEM, source="utility")],
        messages=[HistoryMessage(role="user", content=[{"type": "text", "text": prompt}])],
        max_tokens=max_tokens,
    )


def record_utility_usage(
    context: UtilityContext,
    environment_id: int,
    conversation_id: int | None,
    provider: str,
    completed: Completed,
    task: str | None = None,
    summary: str | None = None,
) -> None:
    with context.session_factory() as session:
        session.add(
            UsageRecord(
                environment_id=environment_id,
                conversation_id=conversation_id,
                provider=provider,
                model=completed.served_by_model,
                purpose="utility",
                task=task,
                summary=summary or None,
                input_tokens=completed.usage.input_tokens,
                output_tokens=completed.usage.output_tokens,
                cache_read_tokens=completed.usage.cache_read_tokens,
                cache_write_tokens=completed.usage.cache_write_tokens,
                cost=usage_cost(context.providers, completed),
                billing=completed.billing,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()


def clean_line(text: str, limit: int) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = line.strip().strip("\"'`*#").strip().rstrip(".")
    return line[:limit].strip()


async def propose_title(
    context: UtilityContext,
    environment_id: int,
    conversation_id: int,
    user_text: str,
    reply_text: str,
    reply_language: str = "",
) -> str | None:
    prompt = f"Write {title_request(reply_language)}.\n\n{first_exchange(user_text, reply_text)}"
    answer = await run_utility(context, environment_id, prompt, conversation_id, max_tokens=60, task="title")
    return clean_title(answer or "")


def title_request(reply_language: str = "") -> str:
    """What a title must be, for a prompt that asks for one, alone or with other work."""
    language = reply_language or "the language of the user's message"
    return f"a title of at most {TITLE_WORDS} words for this conversation, in {language}, with no punctuation at the end"


def first_exchange(user_text: str, reply_text: str) -> str:
    """The first message and reply a title is written from."""
    return f"User:\n{user_text[:2000]}\n\nAssistant:\n{reply_text[:1500]}"


def clean_title(text: str) -> str | None:
    return clean_line(text, 80) or None


async def propose_commit_message(
    context: UtilityContext,
    environment_id: int,
    status: str,
    diff: str,
    work_language: str = "English",
    project_id: int | None = None,
) -> str | UtilitySkipped | None:
    shortened = diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + "\n[Diff shortened.]"
    prompt = (
        f"Write a git commit message in {work_language} for these changes. First line: a summary in the imperative mood of "
        "at most 72 characters. Then, if the change is not trivial, a blank line and a few short bullet points "
        "about what changed and why. Reply with the message only.\n\n"
        f"Changed files:\n{status}\n\nDiff:\n{shortened}"
    )
    answer = await run_utility_or_skip(
        context, environment_id, prompt, max_tokens=500, task="commit_message", project_id=project_id
    )
    if not answer or isinstance(answer, UtilitySkipped):
        return answer or None
    return answer.strip().strip("`").strip()


async def propose_pull_request(
    context: UtilityContext,
    environment_id: int,
    branch: str,
    base: str,
    commits: str,
    files: str,
    work_language: str = "English",
    project_id: int | None = None,
) -> tuple[str, str] | UtilitySkipped | None:
    """A pull request title and description from the branch's commits and changed files."""
    prompt = (
        f"Write a GitHub pull request in {work_language} for merging the branch {branch} into {base}. First line: the "
        "title, at most 70 characters, without a prefix. Then a blank line and the description in Markdown: a short "
        "summary of what changes and why, then a few bullet points. Do not invent tests or results that the commits "
        "do not mention. Reply with the title and description only.\n\n"
        f"Commits:\n{commits[:20000] or '(none)'}\n\nChanged files:\n{files[:20000] or '(none)'}"
    )
    answer = await run_utility_or_skip(
        context, environment_id, prompt, max_tokens=900, task="pull_request", project_id=project_id
    )
    if not answer or isinstance(answer, UtilitySkipped):
        return answer or None
    lines = answer.strip().strip("`").strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines else ""
    return title[:200], "\n".join(lines[1:]).strip()
