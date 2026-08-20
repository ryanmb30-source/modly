"""Shared-secret authentication for the local API (issue #9).

The backend binds 127.0.0.1, which keeps it off the LAN but does nothing about
the browser: any page the user has open can send requests to a loopback port.
CORS was narrowed to the renderer's origins in 04abc5d, but `Origin: null` is
what a `file://` renderer sends *and* what a sandboxed iframe on a hostile page
sends, so an origin check cannot authenticate the caller.

Electron mints a random token when it spawns this process and passes it in the
environment. Every caller that legitimately reaches this API can read it --
the renderer through the preload bridge, the main process directly, and the
backend's own self-calls from the same environment. A web page cannot, so it is
locked out even though it can still open a socket.

Two ways to present it:

- `X-Modly-Token` header, used everywhere a request is built by hand.
- `token` query parameter, for URLs handed to loaders that cannot set headers
  (three.js/drei build their own requests for meshes and splats).

The query form is why `redact_token` exists: without it the secret would be
written to the access log on every mesh load.
"""

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

TOKEN_HEADER = "X-Modly-Token"

# Not "token": /model/hf-download and /model/hf-file already take a `token`
# query parameter carrying the *HuggingFace* token. Reusing the name would make
# this middleware read an HF token as an API token and reject every download.
TOKEN_QUERY_PARAM = "modly_token"

ENV_TOKEN = "MODLY_API_TOKEN"
ENV_ALLOW_UNAUTHENTICATED = "MODLY_ALLOW_UNAUTHENTICATED"

# /health is how the Electron bridge decides the backend has finished starting.
# Leaving it open keeps a misconfigured token a diagnosable 401 on real routes
# rather than a startup that silently never becomes ready. It discloses nothing.
EXEMPT_PATHS = frozenset({"/health"})


def configured_token() -> str | None:
    """The expected token, or None when the process was started without one.

    Read from the environment on every call rather than captured at import.
    A module-level snapshot is the exact bug this codebase already hit with
    WORKSPACE_DIR, and it would also make this untestable without reimporting.
    """
    token = os.environ.get(ENV_TOKEN, "").strip()
    return token or None


def unauthenticated_allowed() -> bool:
    """Escape hatch for running `uvicorn main:app` by hand during development.

    Deliberately opt-in. If an unset token simply meant "no auth", a packaging
    bug that dropped the environment variable would disable authentication
    everywhere and nothing would fail visibly.
    """
    return os.environ.get(ENV_ALLOW_UNAUTHENTICATED, "").strip().lower() in {"1", "true", "yes"}


def presented_token(request: Request) -> str | None:
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header.strip() or None
    query = request.query_params.get(TOKEN_QUERY_PARAM)
    if query:
        return query.strip() or None
    return None


def token_matches(expected: str, presented: str | None) -> bool:
    if not presented:
        return False
    # Constant-time: the token is a fixed per-run secret, so a comparison that
    # returns early would leak it a character at a time to a caller that can
    # retry cheaply over loopback.
    return hmac.compare_digest(expected, presented)


def redact_token(text: str) -> str:
    """Blank the token's value wherever it appears as a query parameter.

    Applied to access-log lines, which would otherwise record the secret on
    every asset request the 3D viewer makes.
    """
    token = configured_token()
    if not token:
        return text
    return text.replace(f"{TOKEN_QUERY_PARAM}={token}", f"{TOKEN_QUERY_PARAM}=REDACTED")


async def api_token_middleware(request: Request, call_next):
    """Rejects any request that cannot present the token.

    Runs as middleware rather than a router dependency so that routes added
    later are covered by default. Forgetting to opt a new router in is the
    failure mode this is meant to avoid.
    """
    if request.url.path in EXEMPT_PATHS:
        return await call_next(request)

    # The browser sends a preflight without credentials or custom headers, so it
    # could never carry the token. CORSMiddleware answers it; let it through.
    if request.method == "OPTIONS":
        return await call_next(request)

    expected = configured_token()
    if expected is None:
        if unauthenticated_allowed():
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    f"{ENV_TOKEN} is not set, so this API cannot authenticate callers "
                    f"and is refusing every request. Electron sets it when it spawns "
                    f"the backend. To run the backend by hand, set "
                    f"{ENV_ALLOW_UNAUTHENTICATED}=1."
                )
            },
        )

    if not token_matches(expected, presented_token(request)):
        return JSONResponse(
            status_code=401,
            content={"detail": f"Missing or invalid {TOKEN_HEADER}."},
        )

    return await call_next(request)
