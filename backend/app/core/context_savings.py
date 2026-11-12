"""The context_savings setting: whether YAAP sends less of what the model already has.

One switch for every part, so an eval comparison turns them on together: a repeated read of an unchanged file
(`agent/repeated_reads.py`), stale reads at a trim point (`core/trimming.py`), test output that keeps what matters
(`agent/test_output.py`) and the repository map kept per conversation (`core/reply_repo_map.py`).
"""

from sqlalchemy.orm import Session

from app.core.settings import SETTING_DEFINITIONS, resolve_setting


def context_savings_on(session: Session, environment_id: int | None) -> bool:
    return resolve_setting(session, SETTING_DEFINITIONS["context_savings"], environment_id).value == "on"
