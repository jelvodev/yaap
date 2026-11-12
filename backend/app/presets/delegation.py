"""How a preset's lead model hands work to workers."""

from typing import Literal, get_args

# free: the lead model delegates as it sees fit. brief: it hands over checkable work with a brief, YAAP runs the check.
DelegationMode = Literal["free", "brief"]
DELEGATION_MODES: tuple[DelegationMode, ...] = get_args(DelegationMode)
DEFAULT_DELEGATION: DelegationMode = "free"


def delegation_mode(value: object) -> DelegationMode:
    """The mode a stored value stands for; anything unknown is free."""
    return "brief" if value == "brief" else DEFAULT_DELEGATION
