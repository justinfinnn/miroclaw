"""Helpers for cooperative cancellation during long-running background work."""

from __future__ import annotations


class OperationCanceledError(RuntimeError):
    """Raised when a long-running operation is canceled cooperatively."""


class PreparationCanceledError(OperationCanceledError):
    """Raised when a user cancels simulation preparation."""


def raise_if_cancel_requested(
    cancel_event,
    message: str = "Preparation canceled by user.",
    *,
    exc_type: type[RuntimeError] = PreparationCanceledError,
) -> None:
    """Raise the requested cancellation exception when the shared cancel flag is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise exc_type(message)
