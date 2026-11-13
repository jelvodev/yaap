"""SQLAlchemy models. Every schema change needs an Alembic migration.

Importing this package imports every area module, so all tables are registered on `Base.metadata`.
"""

from app.storage.models.base import Base, UtcDateTime, utc_now
from app.storage.models.workspace import Credential, Environment, Project, Setting
from app.storage.models.conversations import Attachment, Conversation, Message, ModelPreset, UsageRecord
from app.storage.models.runs import AgentRunRecord, EvalResult, EvalRun, Replay, RunStep
from app.storage.models.goals import Goal, QueuedMessage, Review
from app.storage.models.execution import (
    Checkpoint,
    CheckpointPin,
    ProcessRecord,
    SearchChunk,
    SharedDocument,
    ShellSessionRecord,
)
from app.storage.models.security import AuditRecord, BackupRun, Secret, Target, VaultEntry, VaultUse
from app.storage.models.memory import (
    KnowledgeDocument,
    LearningProposal,
    MemoryMerge,
    MemorySuggestion,
    MemoryTidyState,
)
from app.storage.models.scheduling import ScheduledRun, ScheduledTask
from app.storage.models.accounts import A2ATask, Account, AuthSession, McpClientConversation, PluginState

__all__ = [
    "A2ATask",
    "Account",
    "AgentRunRecord",
    "Attachment",
    "AuditRecord",
    "AuthSession",
    "BackupRun",
    "Base",
    "Checkpoint",
    "CheckpointPin",
    "Conversation",
    "Credential",
    "Environment",
    "EvalResult",
    "EvalRun",
    "Goal",
    "KnowledgeDocument",
    "LearningProposal",
    "McpClientConversation",
    "MemoryMerge",
    "MemorySuggestion",
    "MemoryTidyState",
    "Message",
    "ModelPreset",
    "PluginState",
    "ProcessRecord",
    "Project",
    "QueuedMessage",
    "Replay",
    "Review",
    "RunStep",
    "ScheduledRun",
    "ScheduledTask",
    "SearchChunk",
    "Secret",
    "Setting",
    "SharedDocument",
    "ShellSessionRecord",
    "Target",
    "UsageRecord",
    "UtcDateTime",
    "VaultEntry",
    "VaultUse",
    "utc_now",
]
