"""Domain errors of the Blueprint services, for the HTTP layer to translate into responses."""


class BlueprintNotFoundError(Exception):
    """The item, skill or package that was asked for does not exist at this level."""


class BlueprintConflictError(Exception):
    """The request is valid but the level's current state does not allow it."""
