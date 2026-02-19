from dotenv import load_dotenv
from flask import Flask, render_template_string, request, jsonify
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import os
import logging
import re
import json
from functools import wraps
import sqlite3
from datetime import datetime
from typing import Optional, Tuple, Dict, Any
from contextlib import contextmanager
from pathlib import Path

# ✅ Загружаем .env
load_dotenv()

# ✅ Создаём папку data
DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "app.db"))
DB_DIR = os.path.dirname(DB_PATH)
if DB_DIR:
    Path(DB_DIR).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# 🛡️ CONFIGURATION & SECURITY
# ---------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(24).hex())
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
app.config["WTF_CSRF_HEADERS"] = ["X-CSRF-Token"]
csrf = CSRFProtect(app)

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# 💾 DB (SQLite)
# ---------------------------------------------------------
FREE_CREDITS = int(os.environ.get("FREE_CREDITS", 100))
STARTER_PACK_CREDITS = int(os.environ.get("STARTER_PACK_CREDITS", 1000))

ADMIN_GRANT_KEY = os.environ.get("ADMIN_GRANT_KEY")
PAYMENT_ADDRESS_TRC20 = os.environ.get("PAYMENT_ADDRESS_TRC20", "").strip()

# ---------------------------------------------------------
# 🔧 HELPERS
# ---------------------------------------------------------
def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def ensure_data_dir() -> None:
    d = os.path.dirname(DB_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


@contextmanager
def db():
    """Context manager for database connections"""
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        cur = conn.cursor()
        
        # Users table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            ig_user_id TEXT,
            ig_username TEXT,
            plan TEXT NOT NULL DEFAULT 'free',
            credits INTEGER NOT NULL DEFAULT 0,
            session_data TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        
        # Actions table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            action TEXT NOT NULL,
            target_id TEXT,
            delta_credits INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        
        # Payment requests table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            plan TEXT NOT NULL,
            txid TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        
        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_session ON users(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_txid ON payment_requests(txid)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_session ON payment_requests(session_id)")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_requests_txid_unique ON payment_requests(txid)")
        
        conn.commit()


@app.before_request
def _db_bootstrap():
    init_db()


@app.after_request
def set_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


def mask_sensitive(value: str, show: int = 8) -> str:
    """Mask sensitive data for logging"""
    if not value or len(value) <= show:
        return "***"
    return f"{value[:show]}***"


def validate_sessionid(sessionid: str) -> bool:
    """Validate Instagram sessionid format"""
    if not sessionid or len(sessionid) < 5:
        return False
    if not re.match(r"^[A-Za-z0-9%._-]+$", sessionid):
        return False
    return True


def validate_session_id(session_id: str) -> bool:
    """Validate internal session ID format"""
    if not session_id or len(session_id) != 32:
        return False
    if not re.match(r'^[a-f0-9]{32}$', session_id):
        return False
    return True


def validate_txid(txid: str) -> bool:
    """Validate transaction ID format"""
    if not txid:
        return False
    txid = txid.strip()
    if len(txid) < 20 or len(txid) > 128:
        return False
    if not re.match(r"^[A-Za-z0-9]+$", txid):
        return False
    return True


def require_session(f):
    """Decorator to require valid session"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get("X-Session-ID", "").strip()
        
        if not validate_session_id(session_id):
            return jsonify({"success": False, "error": "Invalid session format"}), 401
        
        user = get_user_by_session(session_id)
        if not user:
            return jsonify({"success": False, "error": "Session not found"}), 401
        
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------
# 📊 DATABASE OPERATIONS
# ---------------------------------------------------------
def get_user_by_session(session_id: str) -> Optional[sqlite3.Row]:
    """Get user by session ID"""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE session_id = ?", (session_id,))
        return cur.fetchone()


def save_session_data(session_id: str, session_data: Dict[str, Any]) -> None:
    """Save session data to database"""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET session_data = ?, updated_at = ?
            WHERE session_id = ?
        """, (json.dumps(session_data), now_iso(), session_id))
        conn.commit()


def load_session_data(session_id: str) -> Optional[Dict[str, Any]]:
    """Load session data from database"""
    user = get_user_by_session(session_id)
    if not user or not user["session_data"]:
        return None
    try:
        return json.loads(user["session_data"])
    except (json.JSONDecodeError, TypeError):
        return None


def upsert_user_on_login(
    session_id: str,
    ig_user_id: str,
    ig_username: str,
    session_data: Dict[str, Any]
) -> None:
    """Create or update user on login"""
    with db() as conn:
        cur = conn.cursor()
        ts = now_iso()

        cur.execute("SELECT * FROM users WHERE session_id = ?", (session_id,))
        row = cur.fetchone()

        if row is None:
            cur.execute("""
                INSERT INTO users(
                    session_id, ig_user_id, ig_username, plan, credits,
                    session_data, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?)
            """, (
                session_id, str(ig_user_id), ig_username, "free",
                FREE_CREDITS, json.dumps(session_data), ts, ts
            ))
            logger.info(
                f"DB: created user @{ig_username} "
                f"(session: {mask_sensitive(session_id)}) "
                f"with {FREE_CREDITS} free credits"
            )
        else:
            cur.execute("""
                UPDATE users
                SET ig_user_id=?, ig_username=?, session_data=?, updated_at=?
                WHERE session_id=?
            """, (
                str(ig_user_id), ig_username,
                json.dumps(session_data), ts, session_id
            ))
            logger.info(
                f"DB: updated user @{ig_username} "
                f"(session: {mask_sensitive(session_id)})"
            )

        conn.commit()


def can_unfollow(user_row: Optional[sqlite3.Row]) -> Tuple[bool, Optional[str]]:
    """Check if user can perform unfollow action"""
    if user_row is None:
        return False, "no_user"
    if user_row["plan"] == "lifetime":
        return True, None
    if int(user_row["credits"]) <= 0:
        return False, "no_credits"
    return True, None


def spend_credit(session_id: str, target_id: str, delta: int) -> bool:
    """
    Atomically spend credits with validation
    Returns True if successful, False if insufficient credits
    """
    with db() as conn:
        cur = conn.cursor()
        ts = now_iso()
        
        # Atomic update with credit check
        cur.execute("""
            UPDATE users
            SET credits = credits + ?, updated_at = ?
            WHERE session_id = ?
              AND plan != 'lifetime'
              AND credits + ? >= 0
        """, (int(delta), ts, session_id, int(delta)))
        
        if cur.rowcount == 0:
            # Either user not found, lifetime plan, or insufficient credits
            cur.execute("SELECT plan, credits FROM users WHERE session_id = ?", (session_id,))
            user = cur.fetchone()
            if user and user["plan"] != "lifetime" and int(user["credits"]) + delta < 0:
                logger.warning(
                    f"Insufficient credits for session {mask_sensitive(session_id)}"
                )
                return False
        
        # Log action
        cur.execute("""
            INSERT INTO actions(session_id, action, target_id, delta_credits, created_at)
            VALUES(?,?,?,?,?)
        """, (session_id, "unfollow", str(target_id), int(delta), ts))
        
        conn.commit()
        return True


# ---------------------------------------------------------
# 🔌 INSTAGRAM CLIENT
# ---------------------------------------------------------
@contextmanager
def get_instagram_client(session_id: str):
    """Context manager for Instagram client"""
    session_data = load_session_data(session_id)
    if not session_data:
        raise ValueError("Session data not found")
    
    cl = None
    try:
        cl = Client()
        if "device_settings" in session_data:
            cl.set_settings(session_data["device_settings"])
        cl.login_by_sessionid(session_data["sessionid"])
        
        # Validate session
        cl.account_info()
        
        yield cl
    except LoginRequired:
        logger.error(f"Instagram session expired for {mask_sensitive(session_id)}")
        raise
    except Exception as e:
        logger.error(f"Instagram client error: {e}")
        raise
    finally:
        if cl:
            # Cleanup if needed
            pass


# ---------------------------------------------------------
# 🖥️ HTML (без изменений, ваш темплейт)
# ---------------------------------------------------------
HTML = r"""
[ВАШ HTML КОД БЕЗ ИЗМЕНЕНИЙ]
"""

# Используйте ваш существующий HTML темплейт


# ---------------------------------------------------------
# 🛣️ ROUTES
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template_string(
        HTML,
        starter_credits=STARTER_PACK_CREDITS,
        pay_addr=PAYMENT_ADDRESS_TRC20
    )


@app.route("/login", methods=["POST"])
@csrf.exempt  # Временно для теста
@limiter.limit("10 per hour")
def login():
    """User login via Instagram sessionid"""
    try:
        data = request.get_json()
        if not data:
            logger.error("Login: No JSON data received")
            return jsonify({"success": False, "error": "No data"}), 400

        sessionid = data.get("cookies", "").strip()
        if not validate_sessionid(sessionid):
            logger.error(f"Login: Invalid sessionid format")
            return jsonify({
                "success": False,
                "error": "Invalid sessionid format"
            }), 400

        logger.info("Login attempt started")
        
        cl = Client()
        try:
            # ✅ БЕЗ timeout - instagrapi имеет встроенные таймауты
            cl.login_by_sessionid(sessionid)
            user_info = cl.account_info()

            device_settings = cl.get_settings()
            session_id = os.urandom(16).hex()

            session_data = {
                "sessionid": sessionid,
                "device_settings": device_settings,
                "user_id": user_info.pk,
                "username": user_info.username
            }

            upsert_user_on_login(
                session_id,
                user_info.pk,
                user_info.username,
                session_data
            )

            logger.info(f"Login successful: @{user_info.username}")
            return jsonify({
                "success": True,
                "session_id": session_id,
                "username": user_info.username
            })

        except LoginRequired as e:
            logger.error(f"Login failed: Session expired - {e}")
            return jsonify({
                "success": False,
                "error": "Session expired. Get a fresh cookie."
            }), 401
        except Exception as e:
            logger.error(f"Login failed: {e}", exc_info=True)
            return jsonify({
                "success": False,
                "error": f"Login failed: {str(e)}"
            }), 500

    except Exception as e:
        logger.error(f"System error in login: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Server error"}), 500


# Остальные роуты без изменений (добавьте ваши существующие)
# /api/me, /scan, /unfollow, /api/payment/*, /api/admin/*

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
