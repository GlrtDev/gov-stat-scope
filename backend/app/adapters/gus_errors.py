"""GUS adapter exceptions."""

from __future__ import annotations


class GUSAOError(Exception):
    """Base GUS adapter error."""


class GUSNotFoundError(GUSAOError):
    """Raised when a GUS resource is not found."""


class GUSInvalidClientIdError(GUSAOError):
    """Raised when the GUS API client id is invalid."""