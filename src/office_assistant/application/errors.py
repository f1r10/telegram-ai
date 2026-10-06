"""Small application errors used by Phase 2 services."""


class ApplicationError(Exception):
    """Base class for expected application-level failures."""


class AuthorizationDenied(ApplicationError):
    """The current actor is not allowed to perform the operation."""


class InvalidStateTransition(ApplicationError):
    """The requested state transition is not valid for the target."""


class AuthenticationConfigurationError(ApplicationError):
    """Authentication cannot run because required configuration is missing."""


class DomainConflict(ApplicationError):
    """A requested business identity or invariant conflicts with stored state."""


class EntityNotFound(ApplicationError):
    """A requested business entity does not exist."""


class InvalidBusinessValue(ApplicationError):
    """A business value failed deterministic validation."""
