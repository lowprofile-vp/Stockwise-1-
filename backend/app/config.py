import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

load_dotenv()


def normalize_database_url(url):
    """Accept postgres:// (Render) and plain postgresql:// URLs."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if "supabase.co" in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _supabase_configured():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    return bool(url and key)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-jwt-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=12)
    JWT_TOKEN_LOCATION = ["headers"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Supabase is reached through the Data API. SQLAlchemy stays on a local file
    # only when those two settings are absent, which is how the tests and the
    # offline demo run.
    SQLALCHEMY_DATABASE_URI = (
        "sqlite://"
        if _supabase_configured()
        else normalize_database_url(os.getenv("DATABASE_URL", "").strip() or "sqlite:///stockwise_demo.db")
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
    RETURN_RESET_TOKEN = os.getenv("RETURN_RESET_TOKEN", "1") == "1"
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "noreply@stockwise.local")
    REMEMBER_DAYS = 14
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_PUBLISHABLE_KEY = (
        os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    # Optional. Only for projects still on the legacy HS256 JWT secret. Without it the
    # API asks Supabase Auth to validate those tokens instead.
    SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()


class TestingConfig(Config):
    TESTING = True
    PROPAGATE_EXCEPTIONS = False
    SUPABASE_URL = ""
    SUPABASE_PUBLISHABLE_KEY = ""
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    JWT_SECRET_KEY = "test-secret-for-the-automated-suite-only"
    RETURN_RESET_TOKEN = True


class ProductionConfig(Config):
    IS_PRODUCTION = True
    RETURN_RESET_TOKEN = os.getenv("RETURN_RESET_TOKEN", "0") == "1"


config_by_name = {
    "default": Config,
    "development": Config,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
