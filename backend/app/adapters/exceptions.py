class GUSAOError(Exception):
    """Base exception for GUS Adapter errors."""


class GUSNotFoundError(GUSAOError):
    """Raised when a requested resource is not found in the GUS API."""


class GUSAuthenticationError(GUSAOError):
    """Raised when GUS rejects the supplied client identifier."""


class GUSRateLimitError(GUSAOError):
    """Raised when GUS rate limiting is exceeded and no retry remains."""

class GUSServerError(GUSAOError):
    """Raised when GUS returns a temporary server-side error."""