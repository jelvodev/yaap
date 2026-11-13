"""The allowed values of goal and message statuses and message roles, shared by services, storage and the API.

This module imports nothing from YAAP, so the storage models, the services and the API schemas can all use it.
"""

from typing import Literal, get_args

# done and blocked: the agent ended the goal (finish_goal); limit: its turns or cost were used up; stopped: by the
# user, a failed reply or a new run of its scheduled task.
EndedGoalStatus = Literal["done", "blocked", "limit", "stopped"]
GoalStatus = Literal["active", "paused", "done", "blocked", "limit", "stopped"]
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["complete", "incomplete", "refused", "error"]

GOAL_STATUSES: tuple[GoalStatus, ...] = get_args(GoalStatus)
MESSAGE_ROLES: tuple[MessageRole, ...] = get_args(MessageRole)
MESSAGE_STATUSES: tuple[MessageStatus, ...] = get_args(MessageStatus)
