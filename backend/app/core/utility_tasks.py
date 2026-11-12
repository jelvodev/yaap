"""Which model a utility task runs on.

Each small task can have a model of its own in the setting `utility_task_models` (a dictionary from task to model,
merged over Global and the environment); a task without one, or with an empty value, uses the utility model. Only
the tasks in ROUTABLE_TASKS can be routed. The check of outside content (`injection_check`) is a security function
and always runs on the utility model of the conversation's preset or the utility_model setting, never on a cheaper
per-task model; summaries use the worker model and the rubric scorer chooses its own model, so neither is routable
either. The code review's model is used only when it is of another vendor than the code's (core/other_vendor.py).
"""

from collections.abc import Collection, Mapping
from typing import Any

from app.providers.registry import ProviderRegistry

SETTING_KEY = "utility_task_models"

# The tasks a per-task model can take over, with the label Settings shows.
ROUTABLE_TASKS: dict[str, str] = {
    "title": "Conversation titles",
    "commit_message": "Commit messages",
    "pull_request": "Pull request texts",
    "memory_suggestion": "Memory suggestions",
    "memory_tidy": "Memory tidying",
    "rule": "Rules from a message",
    "import": "Front matter on import",
    "eval_case": "Eval case proposals",
    "route": "Reply routing",
    "learning": "Learning proposals",
    "review": "Code reviews",
}
# A call that does two tasks at once runs on the model of the more demanding one: the title of a new conversation
# that comes with its first memory suggestions runs on the memory suggestions' model.
ROUTED_AS: dict[str, str] = {"title_and_memory": "memory_suggestion"}
# Tasks that never leave the capable utility model, whatever the setting holds.
CAPABLE_ONLY: frozenset[str] = frozenset({"injection_check"})
# What the app calls each task when it says one was skipped: the routable ones and those that cannot be routed.
TASK_LABELS: dict[str, str] = {
    **ROUTABLE_TASKS,
    "title_and_memory": "Conversation titles and memory suggestions",
    "injection_check": "Checks of outside content",
    "summary": "Summaries",
    "eval_rubric": "Eval rubric scores",
}
# The tasks that start on their own and can be switched off. The user-started ones (rule, import, eval_case,
# commit_message, pull_request) have no switch, and neither do the outside-content check, summaries or rubric scores.
SWITCHABLE_TASKS: dict[str, str] = {
    task: TASK_LABELS[task]
    for task in ("title", "memory_suggestion", "memory_tidy", "route", "learning", "review")
}
OFF_SETTING_KEY = "utility_tasks_off"
# The combined call stands for two switchable tasks: it runs only while both are on, so switching one off leaves the
# other to its own call.
COMBINED_TASKS: dict[str, tuple[str, ...]] = {"title_and_memory": ("title", "memory_suggestion")}


def task_enabled(task: str | None, off: Collection[str]) -> bool:
    """Whether a background task may run: an unnamed task, the outside-content check and every task without a switch
    always may; a switchable task may unless it is named in off."""
    if task is None:
        return True
    parts = COMBINED_TASKS.get(task, (task,))
    return not any(part in SWITCHABLE_TASKS and part in off for part in parts)


def task_label(task: str | None) -> str:
    """The task's label, for a notice or an error; an unnamed or unknown task is a utility task."""
    return TASK_LABELS.get(task or "", "Utility tasks")


def task_model(task: str | None, task_models: Mapping[str, Any]) -> str | None:
    """The model the setting names for this task, or None when the task uses the utility model: no task, a task that
    cannot be routed (the outside-content check among them), or no model or an empty one for it."""
    if task is None or task in CAPABLE_ONLY:
        return None
    routed = ROUTED_AS.get(task, task)
    if routed not in ROUTABLE_TASKS:
        return None
    model = task_models.get(routed)
    return (model.strip() or None) if isinstance(model, str) else None


def check_task_models(value: Any, providers: ProviderRegistry) -> str | None:
    """A dictionary from routable task to a known model id; an empty model means the utility model."""
    if not isinstance(value, dict):
        return "Give a model per task."
    for task, model in value.items():
        if task in CAPABLE_ONLY:
            return "The check of outside content always runs on the utility model; it cannot have a model of its own."
        if task not in ROUTABLE_TASKS:
            return f"Unknown utility task: {task}."
        if not isinstance(model, str) or (model.strip() and providers.find_model(model.strip()) is None):
            return f"Unknown model for {ROUTABLE_TASKS[task].lower()}."
    return None


def check_tasks_off(value: Any, providers: ProviderRegistry) -> str | None:
    """A list of switchable background tasks."""
    if not isinstance(value, list) or not all(isinstance(task, str) for task in value):
        return "Give a list of background tasks."
    for task in value:
        if task in CAPABLE_ONLY:
            return "The check of outside content cannot be switched off."
        if task not in SWITCHABLE_TASKS:
            return f"Unknown or not switchable background task: {task}."
    return None
