"""StockWise Flask application factory."""

from datetime import date, datetime
from decimal import Decimal

from flask import Flask, g, has_request_context, jsonify
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS
from flask_migrate import Migrate
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException

from app.config import config_by_name
from app.errors import APIError
from app.extensions import db, jwt


class StockwiseJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


migrate = Migrate()


def create_app(config_name=None):
    app = Flask(__name__)
    selected = config_name or app.config.get("ENV") or "default"
    if config_name is None:
        import os

        env = os.getenv("FLASK_ENV", "development")
        selected = "production" if env == "production" else "default"
    app.config.from_object(config_by_name[selected])
    app.json = StockwiseJSONProvider(app)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    _enable_sqlite_foreign_keys(app)

    _check_production_secrets(app)
    CORS(app, resources={r"/api/*": {"origins": _cors_origins(app)}}, supports_credentials=True)

    from app import models  # noqa: F401  (register tables)

    _register_jwt_callbacks(jwt)
    _register_error_handlers(app)
    _register_blueprints(app)
    _register_commands(app)
    _ensure_supabase_column(app)
    _register_data_api(app)
    _create_local_tables(app)
    return app


def _cors_origins(app):
    """Browser origins allowed to call the API.

    A trailing slash (https://app.vercel.app/) never matches the Origin header the
    browser sends, so it is stripped. Entries with * become patterns, for example
    https://*.vercel.app for preview deployments. FRONTEND_URL is always allowed.
    """
    import re

    raw = [item for item in app.config["CORS_ORIGINS"].split(",")]
    raw.append(app.config.get("FRONTEND_URL") or "")
    origins = []
    for item in raw:
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            return "*"
        if "*" in origin:
            origin = "^" + re.escape(origin).replace(r"\*", "[^/]+") + "$"
        if origin not in origins:
            origins.append(origin)
    return origins


def _check_production_secrets(app):
    if app.config.get("TESTING") or not app.config.get("IS_PRODUCTION"):
        return
    from app.config import _supabase_configured

    if _supabase_configured():
        return
    secret = app.config.get("JWT_SECRET_KEY") or ""
    if secret.startswith("dev-only") or len(secret) < 32:
        raise RuntimeError(
            "FLASK_ENV=production needs JWT_SECRET_KEY set to a random string of at least 32 characters "
            "(or SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY for Supabase Auth)."
        )


def _create_local_tables(app):
    """Create missing tables for the SQLAlchemy database (SQLite or DATABASE_URL Postgres).

    With Supabase configured the tables come from supabase_schema.sql instead, and the
    SQLAlchemy engine is an unused in-memory placeholder.
    """
    if app.config.get("TESTING"):
        return
    from app.config import _supabase_configured

    if _supabase_configured():
        return
    with app.app_context():
        try:
            db.create_all()
        except Exception:
            app.logger.exception("Could not create database tables")


def _register_data_api(app):
    @app.before_request
    def _open_data_api():
        from app.data.api import enabled, push_uow

        if enabled():
            from flask import g

            g.stockwise_uow = push_uow()

    @app.teardown_request
    def _close_data_api(_exc):
        if not has_request_context():
            return
        token = getattr(g, "stockwise_uow", None)
        if token is not None:
            from app.data.api import reset_uow

            reset_uow(token)


def _register_blueprints(app):
    from app.routes.analytics import bp as analytics_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.business import bp as business_bp
    from app.routes.misc import bp as misc_bp
    from app.routes.products import bp as products_bp
    from app.routes.reports import bp as reports_bp
    from app.routes.restock import bp as restock_bp
    from app.routes.simulations import bp as simulations_bp
    from app.routes.suppliers import bp as suppliers_bp
    from app.routes.transactions import bp as transactions_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(business_bp, url_prefix="/api")
    app.register_blueprint(products_bp, url_prefix="/api")
    app.register_blueprint(transactions_bp, url_prefix="/api")
    app.register_blueprint(suppliers_bp, url_prefix="/api")
    app.register_blueprint(restock_bp, url_prefix="/api")
    app.register_blueprint(analytics_bp, url_prefix="/api")
    app.register_blueprint(simulations_bp, url_prefix="/api")
    app.register_blueprint(reports_bp, url_prefix="/api")
    app.register_blueprint(misc_bp, url_prefix="/api")


def _register_jwt_callbacks(jwt_manager):
    @jwt_manager.token_in_blocklist_loader
    def check_blocklist(_header, payload):
        from app.models import TokenBlocklist

        found = db.session.query(TokenBlocklist.id).filter_by(jti=payload["jti"]).first()
        return found is not None

    @jwt_manager.revoked_token_loader
    def revoked(_header, _payload):
        return jsonify({"error": "Session has ended. Please log in again."}), 401

    @jwt_manager.expired_token_loader
    def expired(_header, _payload):
        return jsonify({"error": "Session expired. Please log in again."}), 401

    @jwt_manager.invalid_token_loader
    def invalid(_reason):
        return jsonify({"error": "Invalid session. Please log in again."}), 401

    @jwt_manager.unauthorized_loader
    def missing(_reason):
        return jsonify({"error": "Login required."}), 401


def _register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api(err):
        body = {"error": err.message}
        if err.details:
            body["details"] = err.details
        return jsonify(body), err.status

    @app.errorhandler(HTTPException)
    def handle_http(err):
        return jsonify({"error": err.description or err.name}), err.code

    @app.errorhandler(OperationalError)
    def handle_db(_err):
        db.session.rollback()
        return (
            jsonify(
                {
                    "error": "The data store could not be reached."
                }
            ),
            503,
        )

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        if isinstance(err, (APIError, HTTPException)):
            raise err
        db.session.rollback()
        app.logger.exception("Unhandled error")
        if app.debug or app.testing:
            return jsonify({"error": "Internal server error", "details": str(err)}), 500
        return jsonify({"error": "Internal server error"}), 500


def _enable_sqlite_foreign_keys(app):
    from sqlalchemy import event

    with app.app_context():
        engine = db.engine

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):
            if engine.dialect.name != "sqlite":
                return
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()


def _ensure_supabase_column(app):
    """Add supabase_uid to a database created before Supabase Auth was wired in."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite"):
        return
    from sqlalchemy import text

    with app.app_context():
        try:
            rows = db.session.execute(text("PRAGMA table_info(users)")).fetchall()
        except Exception:
            db.session.rollback()
            return
        names = {row[1] for row in rows}
        if not names or "supabase_uid" in names:
            return
        db.session.execute(text("ALTER TABLE users ADD COLUMN supabase_uid VARCHAR(36)"))
        db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_supabase_uid ON users (supabase_uid)"))
        db.session.commit()


def _register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        print("Database tables created.")
