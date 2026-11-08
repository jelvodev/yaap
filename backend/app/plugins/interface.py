"""What a plugin imports from YAAP: one module, so plugins do not reach into YAAP's internals.

    from app.plugins.interface import PluginContext, Tool, schema, STRING

    def register(yaap: PluginContext) -> None:
        yaap.register_tool(Tool("hello_time", "Tell the time.", schema({}, []), "auto", hello_time, scope="always"))

Names here stay stable within a major version of YAAP; anything else in `app` may change without notice.
"""

from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, ToolOutput, schema, text_argument, truncate
from app.credentials.types import CredentialTestResult, CredentialType
from app.plugins.host import PluginContext
from app.providers.base import (
    AgentEvent,
    AgentRequest,
    Completed,
    ModelProvider,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
)
from app.providers.catalog import ModelInfo, ModelPricing, PriceTier
from app.providers.registry import ProviderSpec

__all__ = [
    "STRING",
    "AgentEvent",
    "AgentRequest",
    "AgentRun",
    "Completed",
    "CredentialTestResult",
    "CredentialType",
    "ModelInfo",
    "ModelPricing",
    "ModelProvider",
    "PluginContext",
    "PriceTier",
    "ProviderError",
    "ProviderSpec",
    "ReasoningDelta",
    "TextDelta",
    "TokenUsage",
    "Tool",
    "ToolError",
    "ToolOutput",
    "schema",
    "text_argument",
    "truncate",
]
