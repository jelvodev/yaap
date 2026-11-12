"""The Blueprint version a run started with, kept on its record so a replay can rebuild the same instructions."""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.runs import AgentRun
from app.blueprint.versions import layer_versions, versions_to_json
from app.core.reply_setup import ReplySetup
from app.storage.models import AgentRunRecord

logger = logging.getLogger(__name__)


def record_blueprint_versions(session: Session, setup: ReplySetup, run: AgentRun) -> None:
    """Store the commit of each Blueprint layer on the run's record (project conversations only). Synchronous git:
    call it from a worker thread. A failure is logged; the reply goes on without a record."""
    record_id = run.state.record_id
    if setup.agent_project is None or record_id is None:
        return
    try:
        record = session.get(AgentRunRecord, record_id)
        if record is None:
            return
        record.blueprint_versions = versions_to_json(layer_versions(setup.layers))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.warning("The Blueprint version of run %s could not be stored.", record_id, exc_info=True)
