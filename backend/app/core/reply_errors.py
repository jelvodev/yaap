"""Why a reply cannot start. The message of each error is safe to show to the user as it is.

The HTTP layer turns a `ReplyConflict` into 409 and an `InvalidReplyRequest` into 422; goal mode, the scheduler and
A2A show the message where their run is reported.
"""


class ReplyError(Exception):
    """A reply that cannot start."""


class ReplyConflict(ReplyError):
    """The conversation, its connection or its setup does not allow this reply now."""


class ReplyBusy(ReplyConflict):
    """A reply is already running in the conversation."""


class ReplyUnavailable(ReplyConflict):
    """The model, its key or the computer the conversation works on cannot be used."""


class CommandRefused(ReplyConflict):
    """A command in the message (/check, /goal, /compact) cannot run here or now."""


class InvalidReplyRequest(ReplyError):
    """The request names a message or an attachment it cannot use."""
