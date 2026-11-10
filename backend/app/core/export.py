"""Export a conversation as Markdown (to read or share) or JSON (to keep or process).

Both follow the conversation's current branch. Tool calls appear collapsed in Markdown; reasoning is left out of
Markdown but kept in JSON. Attachments are listed by name; their files are not part of an export.
"""

import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.chat import load_branch
from app.storage.models import Conversation, Environment, Message, Project, UsageRecord

TOOL_OUTPUT_LIMIT = 4000
EXPORT_VERSION = 1


def export_filename(conversation: Conversation, extension: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", conversation.title.lower()).strip("-")[:60] or "conversation"
    return f"yaap-{conversation.id}-{name}.{extension}"


def fence(text: str, language: str = "") -> str:
    """A code fence longer than any backtick run in the text, so the text cannot close it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    marks = "`" * max(3, longest + 1)
    return f"{marks}{language}\n{text.rstrip()}\n{marks}"


def clip(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n… ({len(text) - limit} more characters)"


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def speaker(message: Message, conversation: Conversation) -> str:
    if message.role == "user":
        return "You"
    return f"YAAP · {conversation.agent}" if conversation.agent else "YAAP"


def markdown_block(block: dict[str, Any]) -> str | None:
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "").strip() or None
    if kind == "attachment":
        return f"*Attachment: {block.get('name')} ({human_size(int(block.get('size') or 0))})*"
    if kind == "command":
        return f"<details>\n<summary>/{block.get('name')} expanded to</summary>\n\n{fence(str(block.get('text') or ''))}\n\n</details>"
    if kind == "goal":
        return f"*Goal, turn {block.get('turn')} of {block.get('max_turns')}*"
    if kind == "skill":
        return f"*Pinned skill: {block.get('name')}*"
    if kind == "skill_removed":
        return f"*Skill unpinned: {block.get('name')}*"
    if kind == "tool_call":
        arguments = json.dumps(block.get("input") or {}, indent=2, ensure_ascii=False)
        output = block.get("output")
        status = " (failed)" if block.get("is_error") else ""
        parts = [f"<details>\n<summary>Tool: {block.get('name')}{status}</summary>\n", fence(arguments, "json")]
        if output is not None:
            parts.append(fence(clip(str(output))))
        parts.append("\n</details>")
        return "\n".join(parts)
    if kind == "note":
        return f"*{block.get('text') or ''}*"
    if kind == "steering":
        return "\n".join(f"> {line}" for line in f"**You, while YAAP worked:** {block.get('text') or ''}".splitlines())
    if kind == "summary":
        return f"<details>\n<summary>Earlier messages summarised</summary>\n\n{block.get('text') or ''}\n\n</details>"
    return None  # Reasoning stays out of the readable export.


def to_markdown(session: Session, conversation: Conversation) -> str:
    environment = session.get(Environment, conversation.environment_id)
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    details = [
        f"Environment: {environment.name if environment else conversation.environment_id}",
        f"Project: {project.name}" if project else None,
        f"Agent: {conversation.agent}" if conversation.agent else None,
        f"Model: {conversation.model} ({conversation.effort})",
        f"Started: {conversation.created_at:%Y-%m-%d %H:%M} UTC",
    ]
    lines = [f"# {conversation.title}", "", " · ".join(item for item in details if item), ""]
    for message in load_branch(session, conversation.id, conversation.head_message_id):
        blocks = [rendered for block in message.content or [] if (rendered := markdown_block(block))]
        if message.error:
            blocks.append(f"*Error: {message.error}*")
        elif message.status not in ("complete", "streaming"):
            blocks.append(f"*Reply {message.status}.*")
        if not blocks:
            continue
        lines += [f"## {speaker(message, conversation)}", "", "\n\n".join(blocks), ""]
    return "\n".join(lines).rstrip() + "\n"


def to_json(session: Session, conversation: Conversation) -> dict[str, Any]:
    environment = session.get(Environment, conversation.environment_id)
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    usage = session.execute(
        select(
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
            func.coalesce(func.sum(UsageRecord.cache_read_tokens), 0),
            func.coalesce(func.sum(UsageRecord.cache_write_tokens), 0),
            func.sum(UsageRecord.cost),
        ).where(UsageRecord.conversation_id == conversation.id)
    ).one()
    return {
        "yaap_export": EXPORT_VERSION,
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "environment": environment.name if environment else None,
            "project": project.name if project else None,
            "agent": conversation.agent,
            "pinned_skills": conversation.pinned_skills or [],
            "provider": conversation.provider,
            "model": conversation.model,
            "effort": conversation.effort,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
        },
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "cache_read_tokens": usage[2],
            "cache_write_tokens": usage[3],
            "cost": usage[4],
        },
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "status": message.status,
                "error": message.error,
                "created_at": message.created_at.isoformat(),
                "content": message.content or [],
            }
            for message in load_branch(session, conversation.id, conversation.head_message_id)
        ],
    }
