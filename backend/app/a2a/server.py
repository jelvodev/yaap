"""YAAP as an A2A agent: its agent card and the JSON-RPC endpoint, behind an `a2a` API token.

A task is one reply in a conversation of the token's environment and project, started like a message the user sends
(through the reply service in app/core/reply_service.py), so the permission settings and the injection guard apply
as in the chat. A tool that asks waits for the user in YAAP (the header's count links to the conversation); nothing
gives the task full
access. `message/send` waits up to BLOCKING_SECONDS for the reply (unless the caller asks not to block) and returns
the task; the caller follows a longer task with `tasks/get`. A follow-up message with the task's contextId goes on in
the same conversation. Streaming and push notifications are not offered.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.a2a.client import PROTOCOL_VERSION, parts_text
from app.auth.tokens import TokenGrant, require_api_token
from app.core.api_conversations import ScopeError, in_scope, new_conversation, token_scope
from app.core.chat import add_user_message, make_title
from app.core.reply_errors import ReplyError
from app.core.reply_service import ReplyRequest, ReplyService
from app.core.reply_services import ReplyServices
from app.storage.models import A2ATask, Conversation, Message

router = APIRouter(include_in_schema=False)

BLOCKING_SECONDS = 60.0
MAX_MESSAGE_CHARS = 200_000

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004

TERMINAL = frozenset({"completed", "failed", "canceled", "rejected"})


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


@router.get("/.well-known/agent-card.json")
def agent_card(request: Request, grant: TokenGrant = Depends(require_api_token("a2a"))) -> JSONResponse:
    with request.app.state.session_factory() as session:
        try:
            scope = token_scope(session, grant.environment_id, grant.project_id)
        except ScopeError as error:
            raise HTTPException(409, str(error)) from error
        where = (
            f"the project {scope.project.name} of the environment {scope.environment.name}"
            if scope.project is not None
            else f"the environment {scope.environment.name}, outside a project"
        )
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "name": "YAAP",
            "description": (
                f"YAAP, a self-hosted AI agent, working in {where}. It answers questions and does work with its "
                "tools; actions that need approval wait for its user."
            ),
            "url": f"{base}/a2a",
            "preferredTransport": "JSONRPC",
            "version": request.app.version,
            "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "work",
                    "name": "Work in YAAP",
                    "description": f"Answer and carry out a task in {where}, under its permission settings.",
                    "tags": ["agent", "coding", "project"],
                }
            ],
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearer": []}],
        }
    )


@router.post("/a2a")
async def a2a_endpoint(request: Request, grant: TokenGrant = Depends(require_api_token("a2a"))) -> JSONResponse:
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "Parse error."}}, status_code=400
        )
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return rpc_error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid request.")
    request_id, method = message.get("id"), message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return rpc_error(request_id, INVALID_PARAMS, "params is an object.")
    try:
        if method == "message/send":
            return rpc_result(request_id, await send(request.app, grant, params))
        if method == "tasks/get":
            task_id = task_param(params)
            return rpc_result(request_id, await anyio.to_thread.run_sync(task_out, request.app, grant, task_id))
        if method == "tasks/cancel":
            return rpc_result(request_id, await cancel(request.app, grant, task_param(params)))
        if method in ("message/stream", "tasks/resubscribe"):
            raise RpcError(UNSUPPORTED_OPERATION, "Streaming is not offered; use message/send and tasks/get.")
        if method.startswith("tasks/pushNotificationConfig/"):
            raise RpcError(PUSH_NOT_SUPPORTED, "Push notifications are not offered; use tasks/get.")
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
    except RpcError as error:
        return rpc_error(request_id, error.code, error.message)


def task_param(params: dict[str, Any]) -> str:
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise RpcError(INVALID_PARAMS, "id is the task's id.")
    return task_id


def message_text(message: Any) -> str:
    if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
        raise RpcError(INVALID_PARAMS, "message is an A2A message with parts.")
    text = "\n\n".join(item for item in parts_text(message["parts"]) if item.strip())
    if not text.strip():
        raise RpcError(INVALID_PARAMS, "Send at least one text part.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise RpcError(INVALID_PARAMS, f"The message is longer than {MAX_MESSAGE_CHARS} characters.")
    return text


async def send(app: Any, grant: TokenGrant, params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message")
    text = message_text(message)
    if message.get("taskId"):
        raise RpcError(
            INVALID_PARAMS, "A task takes one message; send a new message with the task's contextId to go on."
        )
    context_id = message.get("contextId")
    if context_id is not None and (not isinstance(context_id, str) or not 0 < len(context_id) <= 100):
        raise RpcError(INVALID_PARAMS, "contextId is a text of at most 100 characters.")
    configuration = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
    task_id, conversation_id = await anyio.to_thread.run_sync(start_task, app, grant, text, context_id)
    if configuration.get("blocking") is not False:
        await wait_for_reply(app, conversation_id, BLOCKING_SECONDS)
    return await anyio.to_thread.run_sync(task_out, app, grant, task_id)


def start_task(app: Any, grant: TokenGrant, text: str, context_id: str | None) -> tuple[str, int]:
    """Store the task and start its reply, as a message from the user would. Runs in a worker thread."""
    state = app.state
    service = ReplyService(ReplyServices.from_state(state))
    with state.session_factory() as session:
        try:
            scope = token_scope(session, grant.environment_id, grant.project_id)
        except ScopeError as error:
            raise RpcError(INTERNAL_ERROR, str(error)) from error
        conversation = None
        if context_id is not None:
            earlier = session.scalars(
                select(A2ATask)
                .where(A2ATask.context_id == context_id, A2ATask.token_id == grant.token_id)
                .order_by(A2ATask.created_at.desc())
            ).first()
            if earlier is not None:
                found = session.get(Conversation, earlier.conversation_id)
                conversation = found if in_scope(found, grant.environment_id, grant.project_id) else None
        context_id = context_id or uuid.uuid4().hex
        if conversation is not None and state.replies.running(conversation.id) is not None:
            raise RpcError(INVALID_PARAMS, "The previous task of this context is still running; wait or cancel it.")
        try:
            if conversation is None:
                conversation = new_conversation(session, state.providers, scope, f"A2A · {make_title(text)}")
                session.commit()
            setup = service.prepare(session, conversation, text)
        except (ScopeError, ReplyError) as error:
            raise RpcError(INTERNAL_ERROR, str(error)) from error
        user_message = add_user_message(session, conversation, text)
        task = A2ATask(
            id=uuid.uuid4().hex,
            context_id=context_id,
            token_id=grant.token_id,
            environment_id=grant.environment_id,
            project_id=grant.project_id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            canceled=False,
            created_at=datetime.now(UTC),
        )
        session.add(task)
        # Like a finished scheduled task: the conversation stays marked until the user opens it.
        conversation.unread = True
        session.commit()
        service.launch(session, setup, ReplyRequest(conversation, user_message))
        return task.id, conversation.id


async def wait_for_reply(app: Any, conversation_id: int, timeout: float) -> None:
    job = app.state.replies.running(conversation_id)
    if job is None or job.task is None:
        return
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(job.task), timeout)


def find_task(session: Any, grant: TokenGrant, task_id: str) -> A2ATask:
    task = session.get(A2ATask, task_id)
    if (
        task is None
        or task.token_id != grant.token_id
        or task.environment_id != grant.environment_id
        or task.project_id != grant.project_id
    ):
        raise RpcError(TASK_NOT_FOUND, "Task not found.")
    return task


def agent_message(task: A2ATask, text: str) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": f"{task.id}-status",
        "taskId": task.id,
        "contextId": task.context_id,
        "parts": [{"kind": "text", "text": text}],
    }


def task_state(app: Any, session: Any, task: A2ATask) -> tuple[str, str]:
    """The task's state and the text that goes with it, read from its reply."""
    reply = session.scalars(
        select(Message)
        .where(Message.parent_id == task.user_message_id, Message.role == "assistant")
        .order_by(Message.id)
    ).first()
    if reply is None:
        if app.state.replies.running(task.conversation_id) is not None:
            run = app.state.runs.for_conversation(task.conversation_id)
            waiting = [approval.summary for approval in run.state.approvals.values()] if run is not None else []
            if waiting:
                return "working", "Waiting for the user's approval in YAAP: " + "; ".join(waiting)
            return "working", ""
        return ("canceled", "The task was canceled.") if task.canceled else ("failed", "The reply was lost.")
    text = "\n\n".join(block.get("text", "") for block in reply.content if block.get("type") == "text").strip()
    if reply.status == "complete":
        return "completed", text
    if task.canceled:
        return "canceled", text or "The task was canceled."
    return "failed", "\n\n".join(part for part in (text, reply.error or "") if part) or "The reply failed."


def task_out(app: Any, grant: TokenGrant, task_id: str) -> dict[str, Any]:
    with app.state.session_factory() as session:
        task = find_task(session, grant, task_id)
        state, text = task_state(app, session, task)
        status: dict[str, Any] = {"state": state, "timestamp": datetime.now(UTC).isoformat()}
        if text:
            status["message"] = agent_message(task, text)
        result: dict[str, Any] = {"kind": "task", "id": task.id, "contextId": task.context_id, "status": status}
        if state == "completed":
            result["artifacts"] = [
                {"artifactId": f"{task.id}-reply", "name": "reply", "parts": [{"kind": "text", "text": text}]}
            ]
        return result


async def cancel(app: Any, grant: TokenGrant, task_id: str) -> dict[str, Any]:
    def mark() -> int:
        with app.state.session_factory() as session:
            task = find_task(session, grant, task_id)
            state, _ = task_state(app, session, task)
            if state in TERMINAL:
                raise RpcError(TASK_NOT_CANCELABLE, f"The task has already ended ({state}).")
            task.canceled = True
            session.commit()
            return task.conversation_id

    conversation_id = await anyio.to_thread.run_sync(mark)
    await app.state.replies.stop(conversation_id)
    return await anyio.to_thread.run_sync(task_out, app, grant, task_id)
