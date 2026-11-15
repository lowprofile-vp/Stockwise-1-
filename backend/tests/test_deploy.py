"""Deployment settings: CORS origins, production secrets, and Supabase token checks."""

import io
import json
from unittest import mock

import jwt
import pytest

from app import _cors_origins, create_app
from app.errors import APIError


def _app(**config):
    app = create_app("testing")
    app.config.update(config)
    return app


def test_cors_origins_drop_trailing_slash_and_allow_patterns():
    app = _app(
        CORS_ORIGINS="https://stockwise.vercel.app/, https://*.vercel.app ,",
        FRONTEND_URL="https://stockwise.vercel.app",
    )
    origins = _cors_origins(app)
    assert origins[0] == "https://stockwise.vercel.app"
    assert origins.count("https://stockwise.vercel.app") == 1
    import re

    assert re.match(origins[1], "https://stockwise-git-main-me.vercel.app")
    assert not re.match(origins[1], "https://evil.example.com")


def test_cors_preflight_allows_configured_origin():
    from app.config import TestingConfig

    with mock.patch.object(TestingConfig, "CORS_ORIGINS", "https://shop.example.com/"):
        app = create_app("testing")
    response = app.test_client().options(
        "/api/health",
        headers={"Origin": "https://shop.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("Access-Control-Allow-Origin") == "https://shop.example.com"


def test_hs256_token_without_secret_is_checked_with_supabase_auth():
    from app.services import supabase_auth

    app = _app(SUPABASE_URL="https://demo.supabase.co", SUPABASE_PUBLISHABLE_KEY="sb_publishable_x", SUPABASE_JWT_SECRET="")
    token = jwt.encode({"sub": "abc", "aud": "authenticated"}, "legacy-secret-legacy-secret-legacy-secret", algorithm="HS256")
    body = json.dumps({"id": "abc", "email": "owner@example.com", "user_metadata": {"name": "Owner"}}).encode()

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with app.test_request_context(), mock.patch.object(supabase_auth, "urlopen", return_value=FakeResponse(body)) as opened:
        payload = supabase_auth.verify_access_token(token)
    assert payload["sub"] == "abc"
    assert payload["email"] == "owner@example.com"
    assert opened.call_args[0][0].full_url == "https://demo.supabase.co/auth/v1/user"


def test_hs256_token_with_secret_is_verified_locally():
    from app.services import supabase_auth

    secret = "legacy-secret-legacy-secret-legacy-secret"
    app = _app(SUPABASE_URL="https://demo.supabase.co", SUPABASE_PUBLISHABLE_KEY="sb_publishable_x", SUPABASE_JWT_SECRET=secret)
    good = jwt.encode({"sub": "abc", "aud": "authenticated"}, secret, algorithm="HS256")
    bad = jwt.encode({"sub": "abc", "aud": "authenticated"}, "another-secret-another-secret-another", algorithm="HS256")
    with app.test_request_context():
        assert supabase_auth.verify_access_token(good)["sub"] == "abc"
        with pytest.raises(APIError) as caught:
            supabase_auth.verify_access_token(bad)
    assert caught.value.status == 401


def test_production_without_supabase_rejects_the_default_jwt_secret():
    from app.config import ProductionConfig

    with mock.patch.object(ProductionConfig, "JWT_SECRET_KEY", "dev-only-jwt-secret-change-me"), mock.patch(
        "app.config._supabase_configured", return_value=False
    ), mock.patch.object(ProductionConfig, "SQLALCHEMY_DATABASE_URI", "sqlite://"):
        with pytest.raises(RuntimeError):
            create_app("production")
