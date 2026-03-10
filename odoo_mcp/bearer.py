"""Bearer token extraction for HTTP transport.

Provides a ContextVar that holds the Bearer token extracted from each
incoming HTTP request, so connection methods can use it per-request.

Token formats
-------------
JSON/2:   ``<api_key>``          — Odoo handles user resolution internally.
XML-RPC:  ``<username>:<api_key>`` — split on the first colon; username is
          required because XML-RPC ``authenticate()`` needs it explicitly.
"""

import contextvars
from typing import Optional, Tuple

current_bearer_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_bearer_token", default=None
)


def parse_xmlrpc_token(token: str) -> Tuple[str, str]:
    """Parse an XML-RPC bearer token in ``username:api_key`` format.

    Splits on the first colon only, so the api_key portion may itself
    contain colons without issue.

    Args:
        token: The raw Bearer token string.

    Returns:
        ``(username, api_key)`` tuple.

    Raises:
        ValueError: If the token does not contain a colon or either part
                    is empty.
    """
    idx = token.find(":")
    if idx <= 0:
        raise ValueError("XML-RPC Bearer token must be in 'username:api_key' format")
    username = token[:idx]
    api_key = token[idx + 1 :]
    if not api_key:
        raise ValueError(
            "XML-RPC Bearer token must be in 'username:api_key' format (api_key is empty)"
        )
    return username, api_key


class BearerExtractMiddleware:
    """ASGI middleware: extract Bearer token → ContextVar for downstream use."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
            token = auth[7:] if auth.startswith("Bearer ") else None
            tok = current_bearer_token.set(token)
            try:
                await self.app(scope, receive, send)
            finally:
                current_bearer_token.reset(tok)
        else:
            await self.app(scope, receive, send)
