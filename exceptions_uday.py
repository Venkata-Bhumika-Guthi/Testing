"""
exceptions.py — Canonical exception hierarchy for a production service.

Every exception the application can raise lives here.  Nothing else.
Callers import from this module; this module imports from nothing internal.

Design rules
------------
1.  ONE source of truth.  No `raise ValueError("not found")` scattered across
    the codebase — always raise a typed exception from this file.
2.  Every exception carries structured context (not just a message string) so
    callers can inspect fields without parsing text.
3.  HTTP status codes are attached to exceptions that cross the API boundary,
    keeping that mapping out of view/controller code.
4.  Every exception is serialisable to a dict for JSON error responses and
    structured log entries.
5.  The hierarchy is shallow (max 2 levels deep) — deep trees rot fast.

Hierarchy
---------
    AppError                        ← base; every exception is an AppError
    ├── ConfigError                 ← bad config at startup; always fatal
    ├── ClientError       (4xx)     ← caller's fault; never retry
    │   ├── ValidationError  400
    │   ├── AuthenticationError 401
    │   ├── AuthorizationError  403
    │   ├── NotFoundError       404
    │   ├── ConflictError       409
    │   ├── GoneError           410
    │   ├── RateLimitError      429
    │   └── UnprocessableError  422
    └── ServerError       (5xx)     ← our fault; may retry
        ├── InternalError       500
        ├── NotImplementedError 501
        ├── DependencyError     502    ← upstream returned garbage
        ├── ServiceUnavailableError 503
        └── TimeoutError        504

Usage
-----
    from exceptions import NotFoundError, ValidationError, DependencyError

    raise NotFoundError("user", resource_id=user_id)

    raise ValidationError(
        "Invalid email address.",
        field="email",
        value=raw_email,
    )

    raise DependencyError(
        "Stripe API returned 500.",
        dependency="stripe",
        upstream_status=500,
    )

    # In your error handler / middleware:
    except AppError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)
"""

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


class ConflictError(ClientError):
    """
    Request conflicts with existing state (e.g. duplicate unique key).
    Maps to 409.

    Example
    -------
        raise ConflictError(
            "A user with this email already exists.",
            conflicting_field="email",
            conflicting_value=email,
        )
    """

    http_status = HTTPStatus.CONFLICT
    code        = "conflict"

    def __init__(
        self,
        message:           str,
        *,
        conflicting_field: Optional[str] = None,
        conflicting_value: Any           = None,
    ) -> None:
        ctx: Dict[str, Any] = {}
        if conflicting_field:
            ctx["conflicting_field"] = conflicting_field
        if conflicting_value is not None:
            ctx["conflicting_value"] = str(conflicting_value)
        super().__init__(message, context=ctx)
        self.conflicting_field = conflicting_field
        self.conflicting_value = conflicting_value


class GoneError(ClientError):
    """
    Resource existed but has been permanently deleted.
    Maps to 410.  Tells crawlers/clients to stop requesting it.

    Example
    -------
        raise GoneError("post", resource_id=post_id)
    """

    http_status = HTTPStatus.GONE
    code        = "gone"

    def __init__(self, resource: str, *, resource_id: Any = None) -> None:
        id_fragment = f" '{resource_id}'" if resource_id is not None else ""
        super().__init__(
            f"{resource.capitalize()}{id_fragment} has been permanently deleted.",
            context={"resource": resource, **({"resource_id": str(resource_id)} if resource_id else {})},
        )
        self.resource    = resource
        self.resource_id = resource_id


class RateLimitError(ClientError):
    """
    Caller has exceeded their rate limit.
    Maps to 429.

    Attributes
    ----------
    retry_after : Seconds until the caller may retry.  Should also be sent
                  as a Retry-After HTTP header.

    Example
    -------
        raise RateLimitError(retry_after=30.0, limit=100, window="1m")
    """

    http_status = HTTPStatus.TOO_MANY_REQUESTS
    code        = "rate_limit_exceeded"

    def __init__(
        self,
        *,
        retry_after: float,
        limit:       Optional[int] = None,
        window:      Optional[str] = None,
    ) -> None:
        msg = f"Rate limit exceeded. Retry after {retry_after:.0f}s."
        ctx: Dict[str, Any] = {"retry_after": retry_after}
        if limit  is not None: ctx["limit"]  = limit
        if window is not None: ctx["window"] = window
        super().__init__(msg, context=ctx)
        self.retry_after = retry_after
        self.limit       = limit
        self.window      = window


class UnprocessableError(ClientError):
    """
    Request is well-formed but semantically invalid.
    Maps to 422.  Use when the request passes schema validation but
    fails business-rule checks that require domain knowledge.

    Example
    -------
        raise UnprocessableError(
            "Cannot transfer funds to the source account.",
            reason="same_account_transfer",
        )
    """

    http_status = HTTPStatus.UNPROCESSABLE_ENTITY
    code        = "unprocessable"

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        ctx = {"reason": reason} if reason else {}
        super().__init__(message, context=ctx)
        self.reason = reason


# ──────────────────────────────────────────────────────────────
# Server errors  (5xx)
# ──────────────────────────────────────────────────────────────

class ServerError(AppError):
    """
    Base for all 5xx errors.  Something on our side went wrong.
    Log with ERROR level.  Consider alerting on-call.
    """
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code        = "server_error"


class InternalError(ServerError):
    """
    Generic unexpected error.  Use sparingly — prefer a more specific
    subclass.  Raised by global exception handlers for unhandled exceptions.

    Example
    -------
        raise InternalError("Unexpected state in order FSM.", state=order.state)
    """

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code        = "internal_error"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message, context=extra if extra else None)


class NotImplementedError(ServerError):  # noqa: A001  (shadows builtin intentionally)
    """
    A code path that hasn't been implemented yet.
    Maps to 501.  Prefer raising this over Python's builtin NotImplementedError
    so it flows through the app's error handling correctly.
    """

    http_status = HTTPStatus.NOT_IMPLEMENTED
    code        = "not_implemented"


class DependencyError(ServerError):
    """
    An upstream dependency (database, external API, cache) misbehaved.
    Maps to 502.  The request may succeed if retried.

    Attributes
    ----------
    dependency       : Name of the failing service ("stripe", "postgres", "redis").
    upstream_status  : HTTP status returned by the upstream, if applicable.
    upstream_message : Raw error message from upstream (do NOT forward to clients).

    Example
    -------
        raise DependencyError(
            "Stripe returned an unexpected error.",
            dependency="stripe",
            upstream_status=500,
            upstream_message=raw_body,
        )
    """

    http_status = HTTPStatus.BAD_GATEWAY
    code        = "dependency_error"

    def __init__(
        self,
        message:          str,
        *,
        dependency:       str,
        upstream_status:  Optional[int] = None,
        upstream_message: Optional[str] = None,
    ) -> None:
        ctx: Dict[str, Any] = {"dependency": dependency}
        if upstream_status  is not None: ctx["upstream_status"]  = upstream_status
        if upstream_message is not None: ctx["upstream_message"] = upstream_message
        super().__init__(message, context=ctx)
        self.dependency       = dependency
        self.upstream_status  = upstream_status
        self.upstream_message = upstream_message


class ServiceUnavailableError(ServerError):
    """
    Service is temporarily unable to handle the request (overloaded,
    deploying, circuit breaker open).  Maps to 503.

    Attributes
    ----------
    retry_after : Optional seconds until the service expects to recover.

    Example
    -------
        raise ServiceUnavailableError("Database pool exhausted.", retry_after=5.0)
    """

    http_status = HTTPStatus.SERVICE_UNAVAILABLE
    code        = "service_unavailable"

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        ctx = {"retry_after": retry_after} if retry_after is not None else {}
        super().__init__(message, context=ctx)
        self.retry_after = retry_after


class TimeoutError(ServerError):  # noqa: A001
    """
    An operation (database query, external call, internal job) timed out.
    Maps to 504.

    Attributes
    ----------
    operation       : Description of what timed out ("db_query", "s3_upload").
    timeout_seconds : The configured limit that was exceeded.

    Example
    -------
        raise TimeoutError(
            "Payment gateway did not respond in time.",
            operation="stripe_charge",
            timeout_seconds=10.0,
        )
    """

    http_status = HTTPStatus.GATEWAY_TIMEOUT
    code        = "timeout"

    def __init__(
        self,
        message:         str,
        *,
        operation:       Optional[str]   = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        ctx: Dict[str, Any] = {}
        if operation       is not None: ctx["operation"]       = operation
        if timeout_seconds is not None: ctx["timeout_seconds"] = timeout_seconds
        super().__init__(message, context=ctx)
        self.operation       = operation
        self.timeout_seconds = timeout_seconds
