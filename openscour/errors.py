class OpenScourError(Exception):
    """Base class for all errors raised by the OpenScour client."""


class APIConnectionError(OpenScourError):
    """The OpenScour server could not be reached."""


class APIStatusError(OpenScourError):
    """The server returned an error response."""

    def __init__(self, message: str, status_code: int, body: object = None):
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.body = body


class AuthenticationError(APIStatusError):
    """The API key is missing or invalid (HTTP 401)."""


class InvalidRequestError(APIStatusError):
    """The request was rejected, e.g. an empty query (HTTP 422)."""


class NoResultsError(APIStatusError):
    """No search backend returned results (HTTP 502)."""


class RateLimitError(APIStatusError):
    """Too many requests (HTTP 429). ``retry_after`` is the seconds the server asked to wait, if it said."""

    def __init__(self, message: str, status_code: int, body: object = None, retry_after: float | None = None):
        super().__init__(message, status_code, body)
        self.retry_after = retry_after
