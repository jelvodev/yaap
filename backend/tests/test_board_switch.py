"""Whether a project has its board on, with the level that decided it, and what is offered."""

import pytest

from app.board.settings import BOARD_PUSH_KEY, PROJECT_BOARD_KEY
from app.board.switch import BoardSwitch, board_enabled, board_push_allowed, board_tools_offered
from app.core.settings import SETTING_DEFINITIONS, store_setting
from tests.board_support import sessions_in

ENVIRONMENT, PROJECT = 1, 5


def put(session, key, value, environment_id=None, project_id=None) -> None:
    store_setting(session, SETTING_DEFINITIONS[key], environment_id, value, project_id)
    session.commit()


def test_the_board_is_on_and_pushing_off_by_default() -> None:
    assert SETTING_DEFINITIONS[PROJECT_BOARD_KEY].default is True
    assert SETTING_DEFINITIONS[BOARD_PUSH_KEY].default is False


@pytest.mark.parametrize(
    ("levels", "enabled", "source"),
    [
        ([], True, "default"),
        ([("global", False)], False, "global"),
        ([("environment", False)], False, "environment"),
        ([("project", False)], False, "project"),
        ([("global", False), ("environment", True)], True, "environment"),
        ([("global", False), ("environment", True), ("project", False)], False, "project"),
        ([("global", True), ("environment", False), ("project", True)], True, "project"),
        ([("global", False), ("project", True)], True, "project"),
        ([("environment", False), ("project", True)], True, "project"),
    ],
)
def test_the_nearest_level_decides_and_the_answer_names_it(tmp_path, levels, enabled, source) -> None:
    sessions = sessions_in(tmp_path)
    with sessions() as session:
        for level, value in levels:
            put(
                session,
                PROJECT_BOARD_KEY,
                value,
                None if level == "global" else ENVIRONMENT,
                PROJECT if level == "project" else None,
            )
        assert board_enabled(session, ENVIRONMENT, PROJECT) == BoardSwitch(enabled, source)


def test_another_projects_or_environments_setting_does_not_apply(tmp_path) -> None:
    sessions = sessions_in(tmp_path)
    with sessions() as session:
        put(session, PROJECT_BOARD_KEY, False, ENVIRONMENT, 99)
        put(session, PROJECT_BOARD_KEY, False, 2)
        assert board_enabled(session, ENVIRONMENT, PROJECT) == BoardSwitch(True, "default")


def test_pushing_is_offered_only_when_the_project_switched_it_on(tmp_path) -> None:
    sessions = sessions_in(tmp_path)
    with sessions() as session:
        assert board_push_allowed(session, ENVIRONMENT, PROJECT) is False
        put(session, BOARD_PUSH_KEY, True, ENVIRONMENT, 99)
        assert board_push_allowed(session, ENVIRONMENT, PROJECT) is False
        put(session, BOARD_PUSH_KEY, True, ENVIRONMENT, PROJECT)
        assert board_push_allowed(session, ENVIRONMENT, PROJECT) is True


@pytest.mark.parametrize(
    ("enabled", "linked", "offered"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_card_tools_need_a_linked_card_and_a_board_that_is_on(enabled, linked, offered) -> None:
    assert board_tools_offered(enabled, linked) is offered


@pytest.mark.parametrize("value", [True, False])
def test_the_switch_setting_accepts_only_true_or_false(value) -> None:
    assert SETTING_DEFINITIONS[PROJECT_BOARD_KEY].validate(value, None) is None


@pytest.mark.parametrize("value", ["true", 1, 0, None, "on"])
def test_the_switch_setting_refuses_anything_else(value) -> None:
    assert SETTING_DEFINITIONS[PROJECT_BOARD_KEY].validate(value, None)
