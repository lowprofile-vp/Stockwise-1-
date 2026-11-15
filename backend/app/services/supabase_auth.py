"""Supabase Auth.

Passwords live in Supabase. StockWise stores the workspace role and business
on its own users row, matched by the Supabase user id (the JWT sub). Tokens
are verified with the project's public signing keys. The publishable key is
only used to call the project; it does not bypass Row Level Security.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
from flask import current_app
from jwt import PyJWKClient

from app.errors import APIError

PASSWORD_MARKER = "supabase-managed"
_jwks_clients = {}


def configured():
    return bool(current_app.config.get("SUPABASE_URL") and current_app.config.get("SUPABASE_PUBLISHABLE_KEY"))


def verify_access_token(token):
    if not configured():
        raise APIError("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.", 500)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise APIError("Invalid session. Please log in again.", 401) from exc
    algorithm = header.get("alg")
    if algorithm == "HS256":
        # Projects on the legacy shared-secret signing key. Verify locally when the
        # secret is configured, otherwise ask Supabase Auth to validate the token.
        secret = current_app.config.get("SUPABASE_JWT_SECRET") or ""
        if secret:
            payload = _decode(token, secret, "HS256")
        else:
            payload = _verify_with_auth_server(token)
    else:
        try:
            payload = _verify_with_jwks(token, algorithm)
        except APIError as exc:
            if exc.status != 503:
                raise
            # Signing keys could not be loaded (network blip, empty key set):
            # fall back to Supabase Auth so a valid session is not rejected.
            payload = _verify_with_auth_server(token)
    if not payload.get("sub"):
        raise APIError("Invalid session. Please log in again.", 401)
    return payload


def _decode(token, key, algorithm):
    try:
        return jwt.decode(token, key, algorithms=[algorithm], audience="authenticated", leeway=10)
    except jwt.ExpiredSignatureError as exc:
        raise APIError("Session expired. Please log in again.", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise APIError("Invalid session. Please log in again.", 401) from exc


def _verify_with_jwks(token, algorithm):
    url = f"{current_app.config['SUPABASE_URL']}/auth/v1/.well-known/jwks.json"
    try:
        client = _jwks_clients.get(url)
        if client is None:
            client = PyJWKClient(
                url,
                headers={
                    "apikey": current_app.config["SUPABASE_PUBLISHABLE_KEY"],
                    "Authorization": f"Bearer {current_app.config['SUPABASE_PUBLISHABLE_KEY']}",
                },
            )
            _jwks_clients[url] = client
        key = client.get_signing_key_from_jwt(token)
    except (jwt.PyJWKClientError, jwt.PyJWKClientConnectionError) as exc:
        current_app.logger.warning("Supabase signing keys unavailable: %s", exc)
        raise APIError("Could not load the Supabase signing keys.", 503) from exc
    except jwt.InvalidTokenError as exc:
        raise APIError("Invalid session. Please log in again.", 401) from exc
    except Exception as exc:
        current_app.logger.exception("Supabase token verification failed")
        if exc.__class__.__name__ == "MissingCryptographyError":
            raise APIError("The API cannot verify this Supabase session yet.", 500) from exc
        raise APIError("Could not load the Supabase signing keys.", 503) from exc
    return _decode(token, key.key, algorithm)


def _verify_with_auth_server(token):
    """Ask Supabase Auth who owns the token. Works for every signing method."""
    url = f"{current_app.config['SUPABASE_URL']}/auth/v1/user"
    outgoing = Request(url, method="GET")
    outgoing.add_header("apikey", current_app.config["SUPABASE_PUBLISHABLE_KEY"])
    outgoing.add_header("Authorization", f"Bearer {token}")
    outgoing.add_header("Accept", "application/json")
    try:
        with urlopen(outgoing, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise APIError("Session expired. Please log in again.", 401) from exc
        raise APIError("Could not verify the Supabase session.", 502) from exc
    except (URLError, OSError, ValueError) as exc:
        raise APIError("Could not reach Supabase to verify the session.", 502) from exc
    if not isinstance(data, dict) or not data.get("id"):
        raise APIError("Invalid session. Please log in again.", 401)
    return {
        "sub": data.get("id"),
        "email": data.get("email"),
        "user_metadata": data.get("user_metadata") or {},
        "role": data.get("role"),
    }
