"""Small database configuration error boundary."""


class DatabaseConfigurationError(ValueError):
    """Raised when required database settings are missing or invalid."""
