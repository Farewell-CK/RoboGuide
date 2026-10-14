"""Certificate-authorized command scope shared by persistence and trusted Controller delegation."""

from __future__ import annotations

from contextvars import ContextVar

HTTP_TENANT: ContextVar[tuple[str, bool] | None] = ContextVar("mission_http_tenant", default=None)


def authenticated_tenant() -> str | None:
    """Return the current authenticated tenant, never a caller-supplied forwarding header."""
    context = HTTP_TENANT.get()
    return context[0] if context is not None else None
