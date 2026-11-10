"""When the search model downloads by itself: after the first start, unless the user switched that off."""

import logging
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.embeddings import OFF
from app.core.first_run import FirstRunService
from app.core.local_models import SEARCH_MODEL, ModelService
from app.core.settings import resolve_text

logger = logging.getLogger(__name__)

AUTOMATIC_KEY = "search_model_download"


def wants_automatic_download(session: Session) -> bool:
    """On unless the user switched it off, and pointless while search by meaning is off altogether."""
    return resolve_text(session, AUTOMATIC_KEY, None) != "off" and resolve_text(session, "embedding_model", None) != OFF


def start_automatic_download(session: Session, models: ModelService) -> bool:
    """Start the download when the setting asks for it and the model is not there. True when one was started."""
    if not wants_automatic_download(session) or models.is_ready(SEARCH_MODEL):
        return False
    models.start_download(SEARCH_MODEL)
    return True


async def start_when_first_start_is_done(
    first_run: FirstRunService, models: ModelService, session_factory: sessionmaker[Session], blueprint_dir: Path
) -> None:
    """At application start: an installation whose first start is over gets the model it does not have yet."""
    try:
        with session_factory() as session:
            if (await first_run.status(session, blueprint_dir)).show:
                return
            start_automatic_download(session, models)
    except Exception:  # noqa: BLE001 - a background task at start; search keeps working by words without the model
        logger.exception("The search model's automatic download could not be started.")
