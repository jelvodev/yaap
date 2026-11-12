"""The instructions of YAAP's own helper prompts, as the user's Blueprint sees them."""

from app.blueprint.builtin import BuiltinName, instructions, shipped_instructions
from app.blueprint.layers import Layer
from app.blueprint.levels import environment_layer, global_layer
from app.core.utility import UtilityContext
from app.storage.models import Environment


def utility_instructions(
    context: UtilityContext, environment_id: int, name: BuiltinName, section: str | None = None
) -> str:
    """The text of a built-in item for a utility call: a replacement in the Global layer or the environment's layer
    when the context knows the Blueprint folder, else the shipped text."""
    if context.blueprint_dir is None:
        return shipped_instructions(name, section)
    layers: list[Layer] = [global_layer(context.blueprint_dir)]
    with context.session_factory() as session:
        environment = session.get(Environment, environment_id)
        if environment is not None:
            layers.append(environment_layer(context.blueprint_dir, environment))
    return instructions(name, layers, section)
