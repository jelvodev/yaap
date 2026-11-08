"""Which Blueprint layers a level sees: global, an environment, or a project in an environment."""

from pathlib import Path

from app.blueprint.layers import PROJECT_LAYER_FOLDER, Layer
from app.projects.service import project_path
from app.storage.models import Environment, Project

GLOBAL_FOLDER = "global"


def global_layer(blueprint_dir: Path) -> Layer:
    root = blueprint_dir / GLOBAL_FOLDER
    return Layer("global", "Global", root, root)


def environment_layer(blueprint_dir: Path, environment: Environment) -> Layer:
    root = blueprint_dir / environment.slug
    return Layer("environment", environment.name, root, root)


def project_layer(workspace_dir: Path, project: Project) -> Layer:
    folder = project_path(workspace_dir, project)
    return Layer("project", project.name, folder / PROJECT_LAYER_FOLDER, folder, f"{PROJECT_LAYER_FOLDER}/")


def layers_for(
    blueprint_dir: Path,
    workspace_dir: Path,
    environment: Environment | None = None,
    project: Project | None = None,
) -> list[Layer]:
    """The layers seen from a level, top first. The last one is the level's own layer."""
    layers = [global_layer(blueprint_dir)]
    if environment is not None:
        layers.append(environment_layer(blueprint_dir, environment))
        if project is not None:
            layers.append(project_layer(workspace_dir, project))
    return layers
