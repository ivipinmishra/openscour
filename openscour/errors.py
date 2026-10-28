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
