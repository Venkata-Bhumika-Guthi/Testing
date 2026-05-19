from __future__ import annotations
 
from http import HTTPStatus
from typing import Any, Dict, Optional
 
 
__all__ = [
    # Base
    "AppError",
    # Config
    "ConfigError",
    # Client (4xx)
    "ClientError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "NotFoundError",
    "ConflictError",
    "GoneError",
    "RateLimitError",
    "UnprocessableError",
    # Server (5xx)
    "ServerError",
    "InternalError",
    "NotImplementedError",
    "DependencyError",
    "ServiceUnavailableError",
    "TimeoutError",
]
 
 
# ──────────────────────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────────────────────
 
class AppError(Exception):
    """
    Root of the application exception tree.
 
    Every exception the application raises is an AppError, which means:
      • Middleware can catch AppError once and handle all known errors.
      • Anything that is NOT an AppError is a genuine unexpected crash.
 
    Attributes
    ----------
    message     : Human-readable description (safe to return to clients).
    http_status : HTTP status code for API responses.
    code        : Machine-readable snake_case error code for clients to switch on.
    context     : Arbitrary key/value bag for structured logging / debugging.
    """
 
    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code:        str = "internal_error"
 
    def __init__(
        self,
        message: str,
        *,
        code:    Optional[str]            = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code    = code or self.__class__.code
        self.context: Dict[str, Any] = context or {}
 
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialise to a dict suitable for a JSON error response body.
 
        The shape is stable — clients can rely on `error` and `message`
        always being present.  `context` is included only in non-production
        environments; callers are responsible for stripping it.
        """
        return {
            "error":   self.code,
            "message": self.message,
            **({"context": self.context} if self.context else {}),
        }
 
    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code!r} "
            f"status={self.http_status} "
            f"message={self.message!r}"
            + (f" context={self.context!r}" if self.context else "")
            + ")"
        )
 
 
# ──────────────────────────────────────────────────────────────
# Config  (no HTTP status — these crash the process at startup)
# ──────────────────────────────────────────────────────────────
 
class ConfigError(AppError):
    """
    Raised when required configuration is missing or invalid at startup.
    Always fatal — the process should exit rather than limp along.
 
    Example
    -------
        if not settings.DATABASE_URL:
            raise ConfigError("DATABASE_URL is required.", key="DATABASE_URL")
    """
 
    code = "config_error"
 
    def __init__(self, message: str, *, key: Optional[str] = None) -> None:
        super().__init__(message, context={"key": key} if key else None)
        self.key = key
 
 
# ──────────────────────────────────────────────────────────────
# Client errors  (4xx)
# ──────────────────────────────────────────────────────────────
 
class ClientError(AppError):
    """
    Base for all 4xx errors.  The caller did something wrong.
    Do not retry.  Do not alert on-call.
    """
    http_status = HTTPStatus.BAD_REQUEST
    code        = "client_error"
 
 
class ValidationError(ClientError):
    """
    Request data failed validation.
 
    Attributes
    ----------
    field : The field that failed (e.g. "email", "items[0].price").
    value : The rejected value (omit sensitive data like passwords).
 
    Example
    -------
        raise ValidationError(
            "Price must be a positive number.",
            field="price",
            value=raw_price,
        )
    """
 
    http_status = HTTPStatus.BAD_REQUEST
    code        = "validation_error"
 
    def __init__(
        self,
        message: str,
        *,
        field:   Optional[str] = None,
        value:   Any           = None,
    ) -> None:
        ctx: Dict[str, Any] = {}
        if field is not None:
            ctx["field"] = field
        if value is not None:
            ctx["value"] = value
        super().__init__(message, context=ctx)
        self.field = field
        self.value = value
 
 
class AuthenticationError(ClientError):
    """
    Identity could not be verified (missing / invalid credentials).
    Maps to 401.  Always include a WWW-Authenticate header in the response.
 
    Example
    -------
        raise AuthenticationError("Bearer token has expired.")
    """
 
    http_status = HTTPStatus.UNAUTHORIZED
    code        = "authentication_error"
 
 
class AuthorizationError(ClientError):
    """
    Identity is valid but lacks permission for this action.
    Maps to 403.
 
    Example
    -------
        raise AuthorizationError(
            "You do not have permission to delete this resource.",
            required_permission="resource:delete",
        )
    """
 
    http_status = HTTPStatus.FORBIDDEN
    code        = "authorization_error"
 
    def __init__(
        self,
        message:              str,
        *,
        required_permission:  Optional[str] = None,
    ) -> None:
        ctx = {"required_permission": required_permission} if required_permission else {}
        super().__init__(message, context=ctx)
        self.required_permission = required_permission
 
 
class NotFoundError(ClientError):
    """
    A requested resource does not exist (or is not visible to this caller).
    Maps to 404.
 
    Example
    -------
        raise NotFoundError("user", resource_id=user_id)
        raise NotFoundError("order", resource_id=order_id, detail="Already archived.")
    """
 
    http_status = HTTPStatus.NOT_FOUND
    code        = "not_found"
 
    def __init__(
        self,
        resource:    str,
        *,
        resource_id: Any            = None,
        detail:      Optional[str]  = None,
    ) -> None:
        id_fragment = f" '{resource_id}'" if resource_id is not None else ""
        msg = f"{resource.capitalize()}{id_fragment} not found."
        if detail:
            msg = f"{msg} {detail}"
        ctx: Dict[str, Any] = {"resource": resource}
        if resource_id is not None:
            ctx["resource_id"] = str(resource_id)
        super().__init__(msg, context=ctx)
        self.resource    = resource
        self.resource_id = resource_id

#testing
