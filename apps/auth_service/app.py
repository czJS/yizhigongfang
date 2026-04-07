import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from typing import Any, Callable, Dict, Optional

import pymysql
import redis as redis_lib
from flask import Flask, Response, g, jsonify, redirect, render_template, request
from flask_cors import CORS
from pymysql.cursors import DictCursor


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return default


def _env_int(*names: str, default: int) -> int:
    raw = _env(*names, default=str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _env_bool(*names: str, default: bool) -> bool:
    raw = _env(*names, default="1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_db: str
    mysql_user: str
    mysql_password: str
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    session_ttl_seconds: int
    code_ttl_seconds: int
    device_limit: int
    admin_secret: str
    admin_email: str
    admin_password: str
    admin_password_hash: str
    admin_session_ttl_seconds: int
    cors_allow_origins: str
    service_port: int
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_use_ssl: bool
    smtp_use_tls: bool
    dev_echo_codes: bool
    auto_init_schema: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mysql_host=_env("MYSQL_HOST", "DB_HOST"),
            mysql_port=_env_int("MYSQL_PORT", "DB_PORT", default=3306),
            mysql_db=_env("MYSQL_DB", "DB_NAME"),
            mysql_user=_env("MYSQL_USER", "DB_USER"),
            mysql_password=_env("MYSQL_PASSWORD", "DB_PASSWORD"),
            redis_host=_env("REDIS_HOST"),
            redis_port=_env_int("REDIS_PORT", default=6379),
            redis_password=_env("REDIS_PASSWORD"),
            redis_db=_env_int("REDIS_DB", default=0),
            session_ttl_seconds=_env_int("AUTH_SESSION_TTL_SECONDS", default=30 * 24 * 60 * 60),
            code_ttl_seconds=_env_int("AUTH_CODE_TTL_SECONDS", default=5 * 60),
            device_limit=_env_int("AUTH_DEVICE_LIMIT", default=2),
            admin_secret=_env("AUTH_ADMIN_SECRET"),
            admin_email=_env("AUTH_ADMIN_EMAIL"),
            admin_password=_env("AUTH_ADMIN_PASSWORD"),
            admin_password_hash=_env("AUTH_ADMIN_PASSWORD_HASH"),
            admin_session_ttl_seconds=_env_int("AUTH_ADMIN_SESSION_TTL_SECONDS", default=12 * 60 * 60),
            cors_allow_origins=_env("AUTH_CORS_ALLOW_ORIGINS", default="*"),
            service_port=_env_int("AUTH_SERVICE_PORT", default=8001),
            smtp_host=_env("AUTH_SMTP_HOST"),
            smtp_port=_env_int("AUTH_SMTP_PORT", default=465),
            smtp_username=_env("AUTH_SMTP_USERNAME"),
            smtp_password=_env("AUTH_SMTP_PASSWORD"),
            smtp_from=_env("AUTH_SMTP_FROM"),
            smtp_use_ssl=_env_bool("AUTH_SMTP_USE_SSL", default=True),
            smtp_use_tls=_env_bool("AUTH_SMTP_USE_TLS", default=False),
            dev_echo_codes=_env_bool("AUTH_DEV_ECHO_CODES", default=False),
            auto_init_schema=_env_bool("AUTH_AUTO_INIT_SCHEMA", default=True),
        )


def _iso(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_text(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def _hash_password(password: str, *, salt: Optional[str] = None, iterations: int = 310000) -> str:
    salt_text = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt_text.encode("utf-8"), int(iterations))
    return f"pbkdf2_sha256${int(iterations)}${salt_text}${dk.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    raw = str(password_hash or "").strip()
    parts = raw.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    _algo, iterations_raw, salt_text, digest_hex = parts
    try:
        iterations = int(iterations_raw)
    except Exception:
        return False
    expected = _hash_password(password, salt=salt_text, iterations=iterations)
    return hmac.compare_digest(expected, raw)


def _email_ok(email: str) -> bool:
    return bool(email and EMAIL_RE.match(email))


def _now() -> datetime:
    return datetime.utcnow()


def _make_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def _make_activation_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = []
    for _ in range(4):
        groups.append("".join(secrets.choice(alphabet) for _ in range(4)))
    return "-".join(groups)


def _normalize_activation_code_type(raw: Any, *, default: str = "standard") -> str:
    value = str(raw or "").strip().lower()
    if value in {"standard", "normal", "标准", "标准激活码"}:
        return "standard"
    if value in {"monthly", "month", "月卡", "月度"}:
        return "monthly"
    if value in {"trial", "试用"}:
        return "trial"
    if value in {"renewal", "续费", "续期"}:
        return "renewal"
    if value in {"test", "testing", "测试", "测试码", "测试激活码"}:
        return "test"
    return default


def _normalize_product_edition(raw: Any, *, default: str = "universal") -> str:
    value = str(raw or "").strip().lower()
    if value in {"lite", "light"}:
        return "lite"
    if value in {"quality", "pro"}:
        return "quality"
    if value in {"universal", "all", "any", "*"}:
        return "universal"
    return default


def _client_ip() -> str:
    raw = request.headers.get("X-Forwarded-For") or request.remote_addr or ""
    return raw.split(",")[0].strip()


def create_db_connection(settings: Settings):
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_db,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
    )


def create_redis_client(settings: Settings):
    return redis_lib.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        db=settings.redis_db,
        decode_responses=True,
    )


SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        email VARCHAR(191) NOT NULL UNIQUE,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        created_at DATETIME NOT NULL,
        last_login_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS email_verification_codes (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        email VARCHAR(191) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        expire_at DATETIME NOT NULL,
        used_at DATETIME NULL,
        request_ip VARCHAR(64) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        INDEX idx_email_created (email, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS activation_codes (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        code VARCHAR(64) NOT NULL UNIQUE,
        type VARCHAR(32) NOT NULL DEFAULT 'standard',
        duration_days INT NOT NULL DEFAULT 30,
        duration_minutes INT NOT NULL DEFAULT 0,
        status VARCHAR(32) NOT NULL DEFAULT 'unused',
        product_edition VARCHAR(32) NOT NULL DEFAULT 'universal',
        used_by_user_id BIGINT NULL,
        used_at DATETIME NULL,
        created_at DATETIME NOT NULL,
        INDEX idx_activation_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS licenses (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        user_id BIGINT NOT NULL UNIQUE,
        license_type VARCHAR(32) NOT NULL DEFAULT 'standard',
        product_edition VARCHAR(32) NOT NULL DEFAULT 'universal',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        start_at DATETIME NOT NULL,
        expire_at DATETIME NOT NULL,
        source_activation_code_id BIGINT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        INDEX idx_license_expire (expire_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        user_id BIGINT NOT NULL,
        device_id VARCHAR(191) NOT NULL,
        device_name VARCHAR(191) NOT NULL DEFAULT '',
        platform VARCHAR(64) NOT NULL DEFAULT '',
        active TINYINT(1) NOT NULL DEFAULT 1,
        last_seen_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE KEY uniq_user_device (user_id, device_id),
        INDEX idx_user_active (user_id, active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit_logs (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        action VARCHAR(64) NOT NULL,
        target_type VARCHAR(64) NOT NULL,
        target_id VARCHAR(191) NOT NULL,
        operator_name VARCHAR(191) NOT NULL DEFAULT '',
        detail_json LONGTEXT NULL,
        created_at DATETIME NOT NULL,
        INDEX idx_action_created (action, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
]


def init_schema(settings: Settings) -> None:
    conn = create_db_connection(settings)
    try:
        with conn.cursor() as cur:
            for stmt in SCHEMA_SQL:
                cur.execute(stmt)
            cur.execute("SHOW COLUMNS FROM activation_codes LIKE %s", ("product_edition",))
            if not cur.fetchone():
                cur.execute("ALTER TABLE activation_codes ADD COLUMN product_edition VARCHAR(32) NOT NULL DEFAULT 'universal' AFTER status")
            cur.execute("SHOW COLUMNS FROM activation_codes LIKE %s", ("duration_minutes",))
            if not cur.fetchone():
                cur.execute("ALTER TABLE activation_codes ADD COLUMN duration_minutes INT NOT NULL DEFAULT 0 AFTER duration_days")
            cur.execute("SHOW COLUMNS FROM licenses LIKE %s", ("product_edition",))
            if not cur.fetchone():
                cur.execute("ALTER TABLE licenses ADD COLUMN product_edition VARCHAR(32) NOT NULL DEFAULT 'universal' AFTER license_type")
        conn.commit()
    finally:
        conn.close()


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)

    if settings.cors_allow_origins == "*":
        CORS(app, resources={r"/api/*": {"origins": "*"}})
    else:
        origins = [part.strip() for part in settings.cors_allow_origins.split(",") if part.strip()]
        CORS(app, resources={r"/api/*": {"origins": origins}})

    if settings.auto_init_schema:
        init_schema(settings)

    rds = create_redis_client(settings)

    def db_conn():
        return create_db_connection(settings)

    def _require_env_ready() -> Optional[Any]:
        missing = []
        if not settings.mysql_host:
            missing.append("MYSQL_HOST")
        if not settings.mysql_db:
            missing.append("MYSQL_DB")
        if not settings.mysql_user:
            missing.append("MYSQL_USER")
        if not settings.redis_host:
            missing.append("REDIS_HOST")
        if missing:
            return jsonify({"error": f"missing required env: {', '.join(missing)}"}), 500
        return None

    def _rate_limit(key: str, ttl_seconds: int, limit: int) -> Optional[Any]:
        cur = rds.incr(key)
        if cur == 1:
            rds.expire(key, ttl_seconds)
        if cur > limit:
            return jsonify({"error": "too many requests"}), 429
        return None

    def _send_email(to_email: str, code: str) -> None:
        if settings.dev_echo_codes:
            return
        if not (settings.smtp_host and settings.smtp_from and settings.smtp_username and settings.smtp_password):
            raise RuntimeError("SMTP env is not configured")
        msg = EmailMessage()
        msg["Subject"] = "秒译出海登录验证码"
        msg["From"] = settings.smtp_from
        msg["To"] = to_email
        msg.set_content(
            f"你的验证码是：{code}\n\n"
            f"该验证码将在 {max(1, settings.code_ttl_seconds // 60)} 分钟后过期。\n"
            "如果这不是你的操作，请忽略本邮件。"
        )
        if settings.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
            return
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls(context=ssl.create_default_context())
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)

    def _get_user_by_id(conn, user_id: int) -> Optional[Dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, status, created_at, last_login_at FROM users WHERE id=%s", (user_id,))
            return cur.fetchone()

    def _get_user_by_email(conn, email: str) -> Optional[Dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, status, created_at, last_login_at FROM users WHERE email=%s", (email,))
            return cur.fetchone()

    def _upsert_user(conn, email: str) -> Dict[str, Any]:
        now = _now()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, status, created_at, last_login_at)
                VALUES (%s, 'active', %s, %s)
                ON DUPLICATE KEY UPDATE status='active', last_login_at=VALUES(last_login_at)
                """,
                (email, now, now),
            )
            cur.execute("SELECT id, email, status, created_at, last_login_at FROM users WHERE email=%s", (email,))
            return cur.fetchone()

    def _get_latest_pending_email_code(conn, email: str) -> Optional[Dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, code_hash, expire_at, used_at, request_ip, created_at
                FROM email_verification_codes
                WHERE email=%s AND used_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (email,),
            )
            return cur.fetchone()

    def _consume_email_code(conn, code_id: int) -> int:
        with conn.cursor() as cur:
            return cur.execute(
                """
                UPDATE email_verification_codes
                SET used_at=%s
                WHERE id=%s AND used_at IS NULL
                """,
                (_now(), code_id),
            )

    def _get_license(conn, user_id: int) -> Optional[Dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, license_type, product_edition, status, start_at, expire_at,
                       source_activation_code_id, created_at, updated_at
                FROM licenses
                WHERE user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row and row["status"] == "active" and row["expire_at"] and row["expire_at"] < _now():
                cur.execute(
                    "UPDATE licenses SET status='expired', updated_at=%s WHERE user_id=%s",
                    (_now(), user_id),
                )
                row["status"] = "expired"
            return row

    def _list_devices(conn, user_id: int) -> list[Dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, device_id, device_name, platform, active, last_seen_at, created_at
                FROM devices
                WHERE user_id=%s AND active=1
                ORDER BY created_at ASC
                """,
                (user_id,),
            )
            return list(cur.fetchall() or [])

    def _touch_device(conn, user_id: int, device_id: str, device_name: str, platform: str, device_aliases: Optional[list[str]] = None) -> Optional[str]:
        if not device_id:
            return None
        devices = _list_devices(conn, user_id)
        aliases = {str(device_id or "").strip()}
        for item in device_aliases or []:
            normalized = str(item or "").strip()
            if normalized:
                aliases.add(normalized)
        exists = next((item for item in devices if str(item["device_id"] or "").strip() in aliases), None)
        now = _now()
        if exists:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE devices
                    SET device_id=%s, device_name=%s, platform=%s, last_seen_at=%s, active=1
                    WHERE user_id=%s AND device_id=%s
                    """,
                    (device_id, device_name or "", platform or "", now, user_id, exists["device_id"]),
                )
            return None
        if len(devices) >= settings.device_limit:
            return "device limit reached"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO devices (user_id, device_id, device_name, platform, active, last_seen_at, created_at)
                VALUES (%s, %s, %s, %s, 1, %s, %s)
                """,
                (user_id, device_id, device_name or "", platform or "", now, now),
            )
        return None

    def _issue_session(user: Dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"user_id": user["id"], "email": user["email"]})
        rds.setex(f"auth:session:{token}", settings.session_ttl_seconds, payload)
        return token

    def _normalized_admin_email() -> str:
        return str(settings.admin_email or "").strip().lower()

    def _admin_login_enabled() -> bool:
        return bool(_normalized_admin_email() and (settings.admin_password_hash or settings.admin_password))

    def _verify_admin_credentials(email: str, password: str) -> bool:
        target = _normalized_admin_email()
        given = str(email or "").strip().lower()
        if not target or not given or not hmac.compare_digest(given, target):
            return False
        if settings.admin_password_hash:
            return _verify_password(password, settings.admin_password_hash)
        if settings.admin_password:
            return hmac.compare_digest(str(password or ""), str(settings.admin_password or ""))
        return False

    def _get_session_payload(token: str) -> Optional[Dict[str, Any]]:
        raw = rds.get(f"auth:session:{token}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _auth_token() -> str:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (request.headers.get("X-Session-Token") or "").strip()

    def _issue_admin_session(email: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"email": str(email or "").strip().lower()})
        rds.setex(f"auth:admin_session:{token}", settings.admin_session_ttl_seconds, payload)
        return token

    def _get_admin_session_payload(token: str) -> Optional[Dict[str, Any]]:
        raw = rds.get(f"auth:admin_session:{token}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _admin_session_token() -> str:
        token = str(request.cookies.get("ygf_admin_session") or "").strip()
        if token:
            return token
        return str(request.headers.get("X-Admin-Session") or "").strip()

    def auth_required(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            token = _auth_token()
            if not token:
                return jsonify({"error": "unauthorized"}), 401
            payload = _get_session_payload(token)
            if not payload:
                return jsonify({"error": "session expired"}), 401
            conn = db_conn()
            try:
                user = _get_user_by_id(conn, int(payload["user_id"]))
            finally:
                conn.close()
            if not user:
                return jsonify({"error": "user not found"}), 401
            g.auth_user = user
            g.auth_token = token
            return fn(*args, **kwargs)

        return wrapper

    def admin_required(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            given = (request.headers.get("X-Admin-Secret") or "").strip()
            if settings.admin_secret and given and hmac.compare_digest(given, settings.admin_secret):
                g.admin_actor = "admin-secret"
                return fn(*args, **kwargs)
            token = _admin_session_token()
            if token:
                payload = _get_admin_session_payload(token)
                email = str((payload or {}).get("email") or "").strip().lower()
                if payload and email and hmac.compare_digest(email, _normalized_admin_email()):
                    g.admin_actor = email
                    g.admin_session_token = token
                    return fn(*args, **kwargs)
            if not settings.admin_secret and not _admin_login_enabled():
                return jsonify({"error": "admin auth is not configured"}), 500
            if token:
                return jsonify({"error": "admin session expired"}), 401
            if _admin_login_enabled():
                return jsonify({"error": "admin login required"}), 401
            return jsonify({"error": "forbidden"}), 403

        return wrapper

    def _license_payload(lic: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not lic:
            return {
                "status": "none",
                "active": False,
                "license_type": "",
                "product_edition": "",
                "start_at": "",
                "expire_at": "",
            }
        return {
            "status": lic["status"],
            "active": lic["status"] == "active",
            "license_type": lic["license_type"],
            "product_edition": _normalize_product_edition(lic.get("product_edition"), default="universal"),
            "start_at": _iso(lic.get("start_at")),
            "expire_at": _iso(lic.get("expire_at")),
        }

    def _activation_status(ac: Optional[Dict[str, Any]]) -> str:
        raw = str((ac or {}).get("status") or "").strip().lower()
        if raw in {"used", "unused", "invalidated"}:
            return raw
        return "unused"

    def _activation_duration_delta(ac: Dict[str, Any]) -> timedelta:
        minutes = max(0, int(ac.get("duration_minutes") or 0))
        days = max(0, int(ac.get("duration_days") or 0))
        if minutes > 0:
            return timedelta(minutes=minutes)
        return timedelta(days=max(1, days))

    def _wants_admin_console() -> bool:
        host = str(request.host or "").strip().lower()
        return host.startswith("admin.")

    def _admin_console_url() -> str:
        host = str(request.host or "").strip().lower()
        base = request.host_url.rstrip("/")
        if host.startswith("auth."):
            return base.replace("//auth.", "//admin.") + "/"
        return base + "/"

    @app.get("/")
    def home():
        if _wants_admin_console():
            return render_template("admin_console.html", admin_host=request.host_url.rstrip("/"))
        return Response("miaoyichuhai auth service ok\n", mimetype="text/plain; charset=utf-8")

    @app.get("/admin")
    def admin_console():
        host = str(request.host or "").strip().lower()
        if host.startswith("auth."):
            return redirect(_admin_console_url(), code=302)
        return render_template("admin_console.html", admin_host=request.host_url.rstrip("/"))

    @app.get("/api/admin/me")
    @admin_required
    def admin_me():
        actor = str(getattr(g, "admin_actor", "") or "").strip()
        return jsonify(
            {
                "ok": True,
                "user": {
                    "email": actor if actor and actor != "admin-secret" else _normalized_admin_email(),
                    "auth_type": "secret" if actor == "admin-secret" else "session",
                },
            }
        )

    @app.post("/api/admin/login")
    def admin_login():
        if not _admin_login_enabled():
            return jsonify({"error": "admin login is not configured"}), 500
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        password = str(payload.get("password") or "")
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        if not password:
            return jsonify({"error": "password is required"}), 400
        limited = _rate_limit(f"auth:admin_login:ip:{_client_ip()}", 10 * 60, 20)
        if limited:
            return limited
        if not _verify_admin_credentials(email, password):
            return jsonify({"error": "invalid admin credentials"}), 401
        token = _issue_admin_session(email)
        resp = jsonify({"ok": True, "user": {"email": email, "auth_type": "session"}})
        resp.set_cookie(
            "ygf_admin_session",
            token,
            max_age=settings.admin_session_ttl_seconds,
            httponly=True,
            secure=bool(request.is_secure),
            samesite="Lax",
            path="/",
        )
        return resp

    @app.post("/api/admin/logout")
    def admin_logout():
        token = _admin_session_token()
        if token:
            rds.delete(f"auth:admin_session:{token}")
        resp = jsonify({"ok": True})
        resp.delete_cookie("ygf_admin_session", path="/")
        return resp

    @app.get("/api/health")
    def health():
        db_ok = False
        redis_ok = False
        err = _require_env_ready()
        if err:
            return err
        try:
            conn = db_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                _ = cur.fetchone()
            db_ok = True
            conn.close()
        except Exception:
            db_ok = False
        try:
            redis_ok = bool(rds.ping())
        except Exception:
            redis_ok = False
        return jsonify({"status": "ok" if db_ok and redis_ok else "degraded", "mysql": db_ok, "redis": redis_ok})

    @app.post("/api/auth/email/send-code")
    def send_code():
        err = _require_env_ready()
        if err:
            return err
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        limited = _rate_limit(f"auth:send:email:{email}", 10 * 60, 5)
        if limited:
            return limited
        ip = _client_ip()
        limited = _rate_limit(f"auth:send:ip:{ip}", 60 * 60, 20)
        if limited:
            return limited
        code = _make_code()
        expire_at = _now() + timedelta(seconds=settings.code_ttl_seconds)
        # During migration away from Redis-backed code verification, clear any stale legacy value.
        rds.delete(f"auth:code:{email}")
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO email_verification_codes (email, code_hash, expire_at, used_at, request_ip, created_at)
                    VALUES (%s, %s, %s, NULL, %s, %s)
                    """,
                    (email, _hash_text(code), expire_at, ip, _now()),
                )
            conn.commit()
        finally:
            conn.close()
        try:
            _send_email(email, code)
        except Exception as exc:
            if settings.dev_echo_codes:
                return jsonify({"ok": True, "ttl_seconds": settings.code_ttl_seconds, "dev_code": code, "warning": str(exc)})
            return jsonify({"error": f"send mail failed: {exc}"}), 500
        resp: Dict[str, Any] = {"ok": True, "ttl_seconds": settings.code_ttl_seconds}
        if settings.dev_echo_codes:
            resp["dev_code"] = code
        return jsonify(resp)

    @app.post("/api/auth/email/login")
    def login():
        err = _require_env_ready()
        if err:
            return err
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        code = str(payload.get("code") or "").strip()
        device_id = str(payload.get("device_id") or "").strip()
        raw_device_aliases = payload.get("device_aliases")
        device_aliases = [str(item or "").strip() for item in raw_device_aliases] if isinstance(raw_device_aliases, list) else []
        device_name = str(payload.get("device_name") or "").strip()
        platform = str(payload.get("platform") or "").strip()
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        if not code:
            return jsonify({"error": "code is required"}), 400
        limited = _rate_limit(f"auth:login:ip:{_client_ip()}", 10 * 60, 20)
        if limited:
            return limited
        conn = db_conn()
        try:
            code_row = _get_latest_pending_email_code(conn, email)
            code_hash = _hash_text(code)
            if (
                not code_row
                or not code_row.get("expire_at")
                or code_row["expire_at"] < _now()
                or not hmac.compare_digest(str(code_row.get("code_hash") or ""), code_hash)
            ):
                conn.rollback()
                return jsonify({"error": "invalid code"}), 400
            user = _upsert_user(conn, email)
            device_err = _touch_device(conn, int(user["id"]), device_id, device_name, platform, device_aliases)
            if device_err:
                conn.rollback()
                return jsonify({"error": device_err, "device_limit": settings.device_limit, "devices": _list_devices(conn, int(user["id"]))}), 409
            consumed = _consume_email_code(conn, int(code_row["id"]))
            if not consumed:
                conn.rollback()
                return jsonify({"error": "invalid code"}), 400
            conn.commit()
            lic = _get_license(conn, int(user["id"]))
        finally:
            conn.close()
        rds.delete(f"auth:code:{email}")
        token = _issue_session(user)
        return jsonify(
            {
                "token": token,
                "user": {
                    "id": user["id"],
                    "email": user["email"],
                    "status": user["status"],
                },
                "license": _license_payload(lic),
                "device_limit": settings.device_limit,
            }
        )

    @app.get("/api/auth/me")
    @auth_required
    def me():
        conn = db_conn()
        try:
            lic = _get_license(conn, int(g.auth_user["id"]))
            devices = _list_devices(conn, int(g.auth_user["id"]))
        finally:
            conn.close()
        return jsonify(
            {
                "user": {
                    "id": g.auth_user["id"],
                    "email": g.auth_user["email"],
                    "status": g.auth_user["status"],
                    "created_at": _iso(g.auth_user.get("created_at")),
                    "last_login_at": _iso(g.auth_user.get("last_login_at")),
                },
                "license": _license_payload(lic),
                "devices": devices,
                "device_limit": settings.device_limit,
            }
        )

    @app.post("/api/auth/logout")
    @auth_required
    def logout():
        rds.delete(f"auth:session:{g.auth_token}")
        return jsonify({"ok": True})

    @app.get("/api/license/current")
    @auth_required
    def license_current():
        conn = db_conn()
        try:
            lic = _get_license(conn, int(g.auth_user["id"]))
            devices = _list_devices(conn, int(g.auth_user["id"]))
        finally:
            conn.close()
        return jsonify(
            {
                "license": _license_payload(lic),
                "devices": devices,
                "device_limit": settings.device_limit,
            }
        )

    @app.post("/api/license/redeem")
    @auth_required
    def license_redeem():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        code = str(payload.get("code") or "").strip().upper()
        client_product_edition = _normalize_product_edition(payload.get("product_edition"), default="")
        if not code:
            return jsonify({"error": "activation code is required"}), 400
        if not client_product_edition:
            return jsonify({"error": "product edition is required"}), 400
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, code, type, duration_days, duration_minutes, status, product_edition
                    FROM activation_codes
                    WHERE code=%s
                    FOR UPDATE
                    """,
                    (code,),
                )
                ac = cur.fetchone()
                if not ac:
                    conn.rollback()
                    return jsonify({"error": "activation code not found"}), 404
                if _activation_status(ac) != "unused":
                    conn.rollback()
                    return jsonify({"error": "activation code already used or inactive"}), 400
                code_product_edition = _normalize_product_edition(ac.get("product_edition"), default="universal")
                if code_product_edition != "universal" and client_product_edition != code_product_edition:
                    conn.rollback()
                    return jsonify({"error": f"activation code is only valid for {code_product_edition} edition"}), 400
                lic = _get_license(conn, int(g.auth_user["id"]))
                now = _now()
                start_base = now
                if lic and lic.get("expire_at") and lic["status"] == "active" and lic["expire_at"] > now:
                    current_product_edition = _normalize_product_edition(lic.get("product_edition"), default="universal")
                    if current_product_edition != code_product_edition:
                        conn.rollback()
                        return jsonify(
                            {
                                "error": "current license is still active; only same-edition renewal is allowed before expiry",
                                "current_product_edition": current_product_edition,
                                "code_product_edition": code_product_edition,
                                "current_expire_at": _iso(lic.get("expire_at")),
                            }
                        ), 400
                    start_base = lic["expire_at"]
                expire_at = start_base + _activation_duration_delta(ac)
                if lic:
                    cur.execute(
                        """
                        UPDATE licenses
                        SET license_type=%s, product_edition=%s, status='active', expire_at=%s,
                            source_activation_code_id=%s, updated_at=%s
                        WHERE user_id=%s
                        """,
                        (ac["type"], code_product_edition, expire_at, ac["id"], now, g.auth_user["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO licenses (
                            user_id, license_type, product_edition, status, start_at, expire_at,
                            source_activation_code_id, created_at, updated_at
                        ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s)
                        """,
                        (g.auth_user["id"], ac["type"], code_product_edition, now, expire_at, ac["id"], now, now),
                    )
                cur.execute(
                    """
                    UPDATE activation_codes
                    SET status='used', used_by_user_id=%s, used_at=%s
                    WHERE id=%s
                    """,
                    (g.auth_user["id"], now, ac["id"]),
                )
            conn.commit()
            lic = _get_license(conn, int(g.auth_user["id"]))
        finally:
            conn.close()
        return jsonify({"ok": True, "license": _license_payload(lic)})

    @app.get("/api/license/devices")
    @auth_required
    def license_devices():
        conn = db_conn()
        try:
            devices = _list_devices(conn, int(g.auth_user["id"]))
        finally:
            conn.close()
        return jsonify({"items": devices, "device_limit": settings.device_limit})

    @app.post("/api/license/devices/unbind")
    @auth_required
    def license_devices_unbind():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            return jsonify({"error": "device_id is required"}), 400
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET active=0, last_seen_at=%s WHERE user_id=%s AND device_id=%s",
                    (_now(), g.auth_user["id"], device_id),
                )
            conn.commit()
            devices = _list_devices(conn, int(g.auth_user["id"]))
        finally:
            conn.close()
        return jsonify({"ok": True, "items": devices})

    @app.get("/api/admin/users")
    @admin_required
    def admin_users():
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.email, u.status, u.created_at, u.last_login_at,
                           l.license_type, l.product_edition, l.status AS license_status, l.expire_at,
                           (
                               SELECT COUNT(*)
                               FROM devices d
                               WHERE d.user_id=u.id AND d.active=1
                           ) AS active_device_count
                    FROM users u
                    LEFT JOIN licenses l ON l.user_id=u.id
                    ORDER BY u.id DESC
                    LIMIT 200
                    """
                )
                rows = list(cur.fetchall() or [])
        finally:
            conn.close()
        return jsonify({"items": rows})

    @app.get("/api/admin/activation-codes")
    @admin_required
    def admin_activation_codes():
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ac.id, ac.code, ac.type, ac.duration_days, ac.duration_minutes, ac.status,
                           ac.product_edition, ac.used_by_user_id, u.email AS used_by_email,
                           ac.used_at, ac.created_at
                    FROM activation_codes ac
                    LEFT JOIN users u ON u.id = ac.used_by_user_id
                    ORDER BY ac.id DESC
                    LIMIT 500
                    """
                )
                rows = list(cur.fetchall() or [])
        finally:
            conn.close()
        return jsonify({"items": rows})

    @app.post("/api/admin/activation-codes")
    @admin_required
    def admin_activation_codes_create():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        count = max(1, min(int(payload.get("count") or 1), 200))
        code_type = _normalize_activation_code_type(payload.get("type"), default="standard")
        duration_days = max(0, int(payload.get("duration_days") or 0))
        duration_minutes = max(0, int(payload.get("duration_minutes") or 0))
        if code_type == "test" and duration_minutes <= 0:
            duration_minutes = 3
        if duration_minutes > 0:
            duration_days = 0
        elif duration_days <= 0:
            duration_days = 30
        product_edition = _normalize_product_edition(payload.get("product_edition"), default="universal")
        now = _now()
        items = []
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                for _ in range(count):
                    code = _make_activation_code()
                    cur.execute(
                        """
                        INSERT INTO activation_codes (code, type, duration_days, duration_minutes, status, product_edition, created_at)
                        VALUES (%s, %s, %s, %s, 'unused', %s, %s)
                        """,
                        (code, code_type, duration_days, duration_minutes, product_edition, now),
                    )
                    items.append(
                        {
                            "code": code,
                            "type": code_type,
                            "duration_days": duration_days,
                            "duration_minutes": duration_minutes,
                            "product_edition": product_edition,
                        }
                    )
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "create_activation_codes",
                        "activation_codes",
                        str(count),
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps(
                            {
                                "count": count,
                                "duration_days": duration_days,
                                "duration_minutes": duration_minutes,
                                "type": code_type,
                                "product_edition": product_edition,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"items": items})

    @app.patch("/api/admin/activation-codes/<code>")
    @admin_required
    def admin_activation_codes_update(code: str):
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return jsonify({"error": "activation code is required"}), 400
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, code, type, duration_days, duration_minutes, status, product_edition, used_by_user_id
                    FROM activation_codes
                    WHERE code=%s
                    FOR UPDATE
                    """,
                    (normalized_code,),
                )
                ac = cur.fetchone()
                if not ac:
                    conn.rollback()
                    return jsonify({"error": "activation code not found"}), 404
                if ac.get("used_by_user_id") or _activation_status(ac) == "used":
                    conn.rollback()
                    return jsonify({"error": "used activation code cannot be edited"}), 409

                code_type = _normalize_activation_code_type(payload.get("type"), default=str(ac.get("type") or "standard"))
                product_edition = _normalize_product_edition(payload.get("product_edition"), default=str(ac.get("product_edition") or "universal"))
                duration_days = int(payload.get("duration_days") if "duration_days" in payload else (ac.get("duration_days") or 0))
                duration_minutes = int(payload.get("duration_minutes") if "duration_minutes" in payload else (ac.get("duration_minutes") or 0))
                invalidate = bool(payload.get("invalidate", False))

                duration_days = max(0, duration_days)
                duration_minutes = max(0, duration_minutes)
                if code_type == "test" and duration_minutes <= 0:
                    duration_minutes = 3
                if duration_minutes > 0:
                    duration_days = 0
                elif duration_days <= 0:
                    duration_days = 30

                next_status = "invalidated" if invalidate else "unused"
                cur.execute(
                    """
                    UPDATE activation_codes
                    SET type=%s, duration_days=%s, duration_minutes=%s, status=%s, product_edition=%s
                    WHERE id=%s
                    """,
                    (code_type, duration_days, duration_minutes, next_status, product_edition, ac["id"]),
                )
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "update_activation_code" if not invalidate else "invalidate_activation_code",
                        "activation_code",
                        normalized_code,
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps(
                            {
                                "code": normalized_code,
                                "type": code_type,
                                "duration_days": duration_days,
                                "duration_minutes": duration_minutes,
                                "status": next_status,
                                "product_edition": product_edition,
                            },
                            ensure_ascii=False,
                        ),
                        _now(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "item": {
                    "code": normalized_code,
                    "type": code_type,
                    "duration_days": duration_days,
                    "duration_minutes": duration_minutes,
                    "status": next_status,
                    "product_edition": product_edition,
                },
            }
        )

    @app.delete("/api/admin/activation-codes/<code>")
    @admin_required
    def admin_activation_codes_delete(code: str):
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return jsonify({"error": "activation code is required"}), 400
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, code, status, used_by_user_id
                    FROM activation_codes
                    WHERE code=%s
                    FOR UPDATE
                    """,
                    (normalized_code,),
                )
                ac = cur.fetchone()
                if not ac:
                    conn.rollback()
                    return jsonify({"error": "activation code not found"}), 404
                if ac.get("used_by_user_id") or _activation_status(ac) == "used":
                    conn.rollback()
                    return jsonify({"error": "used activation code cannot be deleted"}), 409
                cur.execute("DELETE FROM activation_codes WHERE id=%s", (ac["id"],))
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "delete_activation_code",
                        "activation_code",
                        normalized_code,
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps({"code": normalized_code}, ensure_ascii=False),
                        _now(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.post("/api/admin/licenses/freeze")
    @admin_required
    def admin_licenses_freeze():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        freeze = bool(payload.get("freeze", True))
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        conn = db_conn()
        try:
            user = _get_user_by_email(conn, email)
            if not user:
                conn.rollback()
                return jsonify({"error": "user not found"}), 404
            with conn.cursor() as cur:
                updated = cur.execute(
                    "UPDATE licenses SET status=%s, updated_at=%s WHERE user_id=%s",
                    ("frozen" if freeze else "active", _now(), user["id"]),
                )
                if not updated:
                    conn.rollback()
                    return jsonify({"error": "license not found"}), 404
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "freeze_license" if freeze else "unfreeze_license",
                        "user",
                        str(user["id"]),
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps({"email": email, "freeze": freeze}, ensure_ascii=False),
                        _now(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.post("/api/admin/licenses/extend")
    @admin_required
    def admin_licenses_extend():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        days = int(payload.get("days") or 30)
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        if days <= 0:
            return jsonify({"error": "days must be > 0"}), 400
        conn = db_conn()
        try:
            user = _get_user_by_email(conn, email)
            if not user:
                conn.rollback()
                return jsonify({"error": "user not found"}), 404
            lic = _get_license(conn, int(user["id"]))
            now = _now()
            new_expire = now + timedelta(days=days)
            with conn.cursor() as cur:
                if lic:
                    base = lic["expire_at"] if lic.get("expire_at") and lic["expire_at"] > now else now
                    new_expire = base + timedelta(days=days)
                    cur.execute(
                        "UPDATE licenses SET status='active', expire_at=%s, updated_at=%s WHERE user_id=%s",
                        (new_expire, now, user["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO licenses (
                            user_id, license_type, product_edition, status, start_at, expire_at,
                            source_activation_code_id, created_at, updated_at
                        ) VALUES (%s, 'manual', 'universal', 'active', %s, %s, NULL, %s, %s)
                        """,
                        (user["id"], now, new_expire, now, now),
                    )
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "extend_license",
                        "user",
                        str(user["id"]),
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps({"email": email, "days": days}, ensure_ascii=False),
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "expire_at": _iso(new_expire)})

    @app.delete("/api/admin/users/<path:email>")
    @admin_required
    def admin_users_delete(email: str):
        normalized_email = str(email or "").strip().lower()
        if not _email_ok(normalized_email):
            return jsonify({"error": "invalid email"}), 400
        conn = db_conn()
        try:
            user = _get_user_by_email(conn, normalized_email)
            if not user:
                conn.rollback()
                return jsonify({"error": "user not found"}), 404
            with conn.cursor() as cur:
                cur.execute("DELETE FROM devices WHERE user_id=%s", (user["id"],))
                cur.execute("DELETE FROM licenses WHERE user_id=%s", (user["id"],))
                cur.execute("DELETE FROM email_verification_codes WHERE email=%s", (normalized_email,))
                cur.execute("DELETE FROM users WHERE id=%s", (user["id"],))
                cur.execute(
                    """
                    INSERT INTO admin_audit_logs (action, target_type, target_id, operator_name, detail_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "delete_user",
                        "user",
                        str(user["id"]),
                        str(getattr(g, "admin_actor", "") or "admin-secret"),
                        json.dumps({"email": normalized_email}, ensure_ascii=False),
                        _now(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.get("/api/admin/devices")
    @admin_required
    def admin_devices():
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.id, d.user_id, u.email, d.device_id, d.device_name, d.platform,
                           d.active, d.last_seen_at, d.created_at,
                           l.status AS license_status, l.license_type, l.product_edition, l.expire_at
                    FROM devices d
                    INNER JOIN users u ON u.id = d.user_id
                    LEFT JOIN licenses l ON l.user_id = d.user_id
                    ORDER BY d.active DESC, d.last_seen_at DESC, d.id DESC
                    LIMIT 500
                    """
                )
                rows = list(cur.fetchall() or [])
        finally:
            conn.close()
        return jsonify({"items": rows})

    @app.post("/api/admin/devices/unbind")
    @admin_required
    def admin_devices_unbind():
        payload: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()
        device_id = str(payload.get("device_id") or "").strip()
        if not _email_ok(email):
            return jsonify({"error": "invalid email"}), 400
        if not device_id:
            return jsonify({"error": "device_id is required"}), 400
        conn = db_conn()
        try:
            user = _get_user_by_email(conn, email)
            if not user:
                conn.rollback()
                return jsonify({"error": "user not found"}), 404
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET active=0, last_seen_at=%s WHERE user_id=%s AND device_id=%s",
                    (_now(), user["id"], device_id),
                )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    return app


app = create_app()


if __name__ == "__main__":
    settings = Settings.from_env()
    app.run(host="0.0.0.0", port=settings.service_port, debug=False)
