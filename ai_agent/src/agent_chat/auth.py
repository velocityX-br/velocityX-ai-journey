"""IAS (SAP Identity Authentication) OIDC login — BFF pattern.

All OIDC happens server-side; the browser only ever holds a signed,
HttpOnly session cookie. The React SPA calls same-origin ``/api`` and never
sees ``client_id`` / ``client_secret``.

Wiring is opt-in: ``install_auth(app)`` is a no-op unless the required
environment variables are set, so tests and unauthenticated local runs are
unaffected. Set these to enable login:

  * ``IAS_ISSUER``         e.g. https://<tenant>.accounts.ondemand.com
  * ``IAS_CLIENT_ID``      OIDC application client id from the IAS admin console
  * ``IAS_CLIENT_SECRET``  OIDC application client secret
  * ``IAS_REDIRECT_URI``   e.g. http://localhost:3010/auth/callback
  * ``SESSION_SECRET``     random string; signs the session cookie
"""

from __future__ import annotations

import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

# Env vars that must all be present for auth to activate.
_REQUIRED_ENV = ("IAS_ISSUER", "IAS_CLIENT_ID", "IAS_CLIENT_SECRET", "IAS_REDIRECT_URI", "SESSION_SECRET")

oauth = OAuth()
router = APIRouter()


def _configured() -> bool:
    """True only if every required env var is set (non-empty)."""
    return all(os.environ.get(name) for name in _REQUIRED_ENV)


def _register() -> None:
    """Register the IAS provider from its OIDC discovery document."""
    oauth.register(
        name="ias",
        server_metadata_url=f"{os.environ['IAS_ISSUER']}/.well-known/openid-configuration",
        client_id=os.environ["IAS_CLIENT_ID"],
        client_secret=os.environ["IAS_CLIENT_SECRET"],
        client_kwargs={"scope": "openid profile email"},
    )


@router.get("/auth/login")
async def login(request: Request) -> RedirectResponse:
    """Kick off the OIDC authorization-code flow → redirect to IAS."""
    redirect_uri = os.environ["IAS_REDIRECT_URI"]
    return await oauth.ias.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", response_model=None)
async def callback(request: Request) -> RedirectResponse:
    """IAS redirects here with ?code=…; exchange it and store the user in session.

    On failure we redirect back to ``/`` with an ``?auth_error=`` query param
    rather than rendering raw JSON. A dead-end error page would let the SPA's
    login gate re-fire ``/auth/login`` and clobber the pending OIDC ``state``,
    so always hand control back to the app.
    """
    try:
        token = await oauth.ias.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 — surface auth failures via redirect
        from urllib.parse import quote

        return RedirectResponse(url=f"/?auth_error={quote(str(exc))}")
    userinfo = token.get("userinfo") or await oauth.ias.userinfo(token=token)
    request.session["user"] = {
        "sub": userinfo.get("sub"),
        "email": userinfo.get("email"),
        "name": userinfo.get("name"),
    }
    return RedirectResponse(url="/")


@router.get("/auth/me")
async def me(request: Request) -> JSONResponse:
    """Return the current user, or 401 if not logged in. The SPA polls this."""
    user = request.session.get("user")
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True, "user": user})


@router.get("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the local session. (Does not perform IAS single-logout.)"""
    request.session.clear()
    return RedirectResponse(url="/")


def install_auth(app: FastAPI) -> bool:
    """Wire session middleware + OIDC routes onto ``app`` when configured.

    Returns True if auth was installed, False if it was skipped because the
    required environment variables are not all set.
    """
    if not _configured():
        return False
    app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"])
    _register()
    app.include_router(router)
    return True
