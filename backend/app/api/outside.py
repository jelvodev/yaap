"""Screens for outside agents and clients: testing a remote A2A agent, and the approvals outside MCP clients wait for."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.a2a.client import A2AError, probe_agent
from app.a2a.tool import configured_agents
from app.agent.outside_mcp import outside_clients
from app.api.conversations import ConversationDependency
from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.secrets.service import SecretError, fill_in, resolve_secrets

router = APIRouter(tags=["outside"])


class A2aAgentCardOut(BaseModel):
    name: str
    description: str
    endpoint: str
    skills: list[str]


class OutsideApprovalOut(BaseModel):
    approval_id: str
    tool: str
    summary: str
    detail: str
    reason: str | None
    suspicion: str | None


@router.post("/environments/{environment_id}/a2a/{name}/test", response_model=A2aAgentCardOut)
async def test_a2a_agent(
    name: str, environment: EnvironmentDependency, session: SessionDependency, request: Request
) -> A2aAgentCardOut:
    """Read the agent's card with its token: shows that the address and token work, without sending a message."""
    agent = next((item for item in configured_agents(session, environment.id) if item.name == name), None)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No enabled agent with this name.")
    token = None
    if agent.token:
        try:
            token = fill_in(agent.token, resolve_secrets(session, request.app.state.cipher, environment.id, None))
        except SecretError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    session.close()
    try:
        card = await probe_agent(agent.url, token, getattr(request.app.state, "a2a_transport", None))
    except A2AError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return A2aAgentCardOut(name=card.name, description=card.description, endpoint=card.endpoint, skills=list(card.skills))


@router.get("/conversations/{conversation_id}/outside-approvals", response_model=list[OutsideApprovalOut])
def outside_approvals(conversation: ConversationDependency, request: Request) -> list[OutsideApprovalOut]:
    """What an outside MCP client waits for in this conversation; these have no reply stream to show them."""
    return [
        OutsideApprovalOut(
            approval_id=approval.id,
            tool=approval.tool,
            summary=approval.summary,
            detail=approval.detail,
            reason=approval.reason,
            suspicion=approval.suspicion,
        )
        for run in outside_clients(request.app).runs_of(conversation.id)
        for approval in list(run.state.approvals.values())
        if not approval.future.done()
    ]
