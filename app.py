from flask import Flask, render_template_string, request, jsonify
from flask_wtf.csrf import CSRFProtect
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import os
import logging
import re
from functools import wraps
import sqlite3
from datetime import datetime

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(24).hex())
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
app.config["WTF_CSRF_HEADERS"] = ["X-CSRF-Token"]
csrf = CSRFProtect(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# In-memory sessions (will reset on restart and won't work across multiple instances)
user_sessions = {}

# ---------------------------------------------------------
# DB (SQLite)
# ---------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "app.db"))
FREE_CREDITS = int(os.environ.get("FREE_CREDITS", 100))
STARTER_PACK_CREDITS = int(os.environ.get("STARTER_PACK_CREDITS", 1000))

ADMIN_GRANT_KEY = os.environ.get("ADMIN_GRANT_KEY")
PAYMENT_ADDRESS_TRC20 = os.environ.get("PAYMENT_ADDRESS_TRC20", "").strip()


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


def ensure_data_dir():
    d = os.path.dirname(DB_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def db():
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE,
        ig_user_id TEXT,
        ig_username TEXT,
        plan TEXT NOT NULL DEFAULT 'free',      -- free | lifetime
        credits INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        plan TEXT NOT NULL,                          -- starter | lifetime
        txid TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_txid ON payment_requests(txid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_session ON payment_requests(session_id)")
    conn.commit()
    conn.close()


def get_user_by_session(session_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    return row


def upsert_user_on_login(session_id: str, ig_user_id: str, ig_username: str):
    conn = db()
    cur = conn.cursor()
    ts = now_iso()

    cur.execute("SELECT * FROM users WHERE session_id = ?", (session_id,))
    row = cur.fetchone()

    if row is None:
        cur.execute("""
            INSERT INTO users(session_id, ig_user_id, ig_username, plan, credits, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
        """, (session_id, str(ig_user_id), ig_username, "free", FREE_CREDITS, ts, ts))
        logger.info("DB: created user @%s with %s free credits", ig_username, FREE_CREDITS)
    else:
        cur.execute("""
            UPDATE users
            SET ig_user_id=?, ig_username=?, updated_at=?
            WHERE session_id=?
        """, (str(ig_user_id), ig_username, ts, session_id))
        logger.info("DB: updated user @%s", ig_username)

    conn.commit()
    conn.close()


def can_unfollow(user_row):
    if user_row is None:
        return False, "no_user"
    if user_row["plan"] == "lifetime":
        return True, None
    if int(user_row["credits"]) <= 0:
        return False, "no_credits"
    return True, None


def spend_credit(session_id: str, target_id: str, delta: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET credits = credits + ?, updated_at=?
        WHERE session_id=? AND plan != 'lifetime'
    """, (int(delta), now_iso(), session_id))
    cur.execute("""
        INSERT INTO actions(session_id, action, target_id, delta_credits, created_at)
        VALUES(?,?,?,?,?)
    """, (session_id, "unfollow", str(target_id), int(delta), now_iso()))
    conn.commit()
    conn.close()


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def get_instagram_client(session_id):
    if session_id not in user_sessions:
        return None
    session_data = user_sessions[session_id]
    try:
        cl = Client()
        if "device_settings" in session_data:
            cl.set_settings(session_data["device_settings"])
        cl.login_by_sessionid(session_data["sessionid"])
        cl.account_info()
        return cl
    except Exception as e:
        logger.error("Client creation failed: %s", e)
        return None


def validate_sessionid(sessionid: str) -> bool:
    if not sessionid or len(sessionid) < 5:
        return False
    # only value, no ';' etc
    if not re.match(r"^[A-Za-z0-9%._-]+$", sessionid):
        return False
    return True


def extract_sessionid(cookie_str: str) -> str:
    """
    Accepts either:
    - pure sessionid value
    - full Cookie string
    Returns sessionid value or "".
    """
    if not cookie_str:
        return ""
    s = cookie_str.strip()
    if "sessionid=" not in s and ";" not in s:
        return s
    m = re.search(r"(?:^|;\s*)sessionid=([^;]+)", s)
    return m.group(1).strip() if m else ""


def validate_txid(txid: str) -> bool:
    if not txid:
        return False
    txid = txid.strip()
    if len(txid) < 20 or len(txid) > 128:
        return False
    if not re.match(r"^[A-Za-z0-9]+$", txid):
        return False
    return True


def require_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get("X-Session-ID")
        if not session_id or session_id not in user_sessions:
            return jsonify({"success": False, "error": "Invalid session"}), 401
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------
# ERROR HANDLERS (so we see JSON errors on Render)
# ---------------------------------------------------------
@app.errorhandler(500)
def handle_500(e):
    logger.exception("Unhandled 500")
    return jsonify({"success": False, "error": "internal_error"}), 500


@app.errorhandler(Exception)
def handle_any_exception(e):
    logger.exception("Unhandled exception")
    return jsonify({"success": False, "error": type(e).__name__, "detail": str(e)}), 500


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="csrf-token" content="{{ csrf_token() }}">
<title>Unfollow Ninja</title>
<style>
:root { --bg:#000; --card:#111; --text:#fff; --accent:#ff0080; --muted:#888; --line:#222; }
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:20px;min-height:100vh;display:flex;align-items:center;justify-content:center}
.container{width:100%;max-width:720px;background:var(--card);padding:34px 26px;border-radius:24px;box-shadow:0 10px 40px rgba(255,0,128,.10);border:1px solid var(--line);text-align:center}
h1{margin:0 0 6px;font-size:32px;letter-spacing:-1px;background:linear-gradient(to right,#fff,#888);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.subtitle{color:var(--muted);margin-bottom:18px;font-size:14px;line-height:1.4}
.badge{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin:10px 0 12px}
.pill{background:#0b0b0b;border:1px solid #262626;color:#bdbdbd;font-size:12px;padding:7px 10px;border-radius:999px}
input, textarea{width:100%;padding:16px;margin-bottom:12px;background:#1a1a1a;border:1px solid #333;border-radius:12px;color:#fff;font-size:15px;outline:none;box-sizing:border-box;transition:.2s}
textarea{min-height:78px;resize:vertical;font-family:ui-monospace,Menlo,monospace}
button.action-btn{width:100%;padding:16px;background:#fff;color:#000;border:none;border-radius:12px;font-size:15px;font-weight:900;cursor:pointer;transition:.2s;text-transform:uppercase;letter-spacing:1px}
button.action-btn:disabled{opacity:.55;cursor:wait;transform:none}
hr{border:0;border-top:1px solid #1f1f1f;margin:16px 0}
.small{font-size:12px;color:#9aa0aa;line-height:1.4;margin-top:10px}
.log{margin-top:14px;background:#0b0b0b;border:1px solid #222;border-radius:16px;padding:12px;text-align:left;font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#bfe3c6;max-height:180px;overflow:auto}
</style>
</head>
<body>
<div class="container">
  <h1>Unfollow Ninja</h1>
  <div class="subtitle">100 free unfollows per account • Starter $5 (+{{ starter_credits }}) • Lifetime $9</div>

  <div class="badge">
    <div class="pill" id="authState">Not signed in</div>
    <div class="pill" id="quotaState">Plan: — • Credits: —</div>
  </div>

  <div id="loginBox">
    <textarea id="sessionid" placeholder="Paste Instagram sessionid (value) OR full Cookie string here..."></textarea>
    <button onclick="login()" id="loginBtn" class="action-btn">LOGIN</button>
    <div class="small">We never ask for passwords. You get <b>100</b> free unfollows per account.</div>
    <hr>
  </div>

  <div class="log" id="logs"><div>> System ready.</div></div>
</div>

<script>
let currentSessionId = '';
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

function addLog(msg){
  const logs = document.getElementById('logs');
  const time = new Date().toLocaleTimeString();
  logs.innerHTML += `<div><span style="opacity:0.6">[${time}]</span> ${msg}</div>`;
  logs.scrollTop = logs.scrollHeight;
}

async function login(){
  const s = document.getElementById('sessionid').value.trim();
  if(!s) return alert('Paste sessionid');

  const btn = document.getElementById('loginBtn');
  btn.disabled = true;
  btn.textContent = 'VERIFYING...';

  try{
    const payload = { cookies: s };
    addLog("Sending /login JSON payload keys: " + Object.keys(payload).join(", "));

    const res = await fetch('/login', {
      method: 'POST',
      headers: {
        'Content-Type':'application/json',
        'X-CSRF-Token': csrfToken
      },
      body: JSON.stringify(payload)
    });

    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text); } catch(e){
      addLog("Server returned non-JSON: " + text.slice(0,120));
      return;
    }

    if(!data.success){
      addLog('Login failed: ' + (data.error || ('HTTP ' + res.status)) + (data.hint ? (" | " + data.hint) : ""));
      return;
    }

    currentSessionId = data.session_id;
    addLog('Login OK: @' + data.username + " session=" + currentSessionId.slice(0,8) + "…");
  }catch(e){
    addLog('Network error during login');
  } finally {
    btn.disabled = false;
    btn.textContent = 'LOGIN';
  }
}
</script>
</body>
</html>
"""

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template_string(
        HTML,
        starter_credits=STARTER_PACK_CREDITS,
        pay_addr=PAYMENT_ADDRESS_TRC20
    )


@app.route("/login", methods=["POST"])
@csrf.exempt
def login():
    try:
        logger.info("=== /login hit ===")
        logger.info("Content-Type: %s", request.headers.get("Content-Type"))
        logger.info("Content-Length: %s", request.headers.get("Content-Length"))

        raw_body = request.get_data(as_text=True) or ""
        logger.info("Raw body first 200 chars: %r", raw_body[:200])

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({
                "success": False,
                "error": "no_json_body",
                "hint": "Expected JSON with Content-Type application/json"
            }), 400

        raw = (data.get("cookies") or "").strip()
        sessionid = extract_sessionid(raw)

        if not validate_sessionid(sessionid):
            return jsonify({
                "success": False,
                "error": "invalid_sessionid_format",
                "hint": "Paste only sessionid value OR full Cookie string containing sessionid=..."
            }), 400

        cl = Client()
        cl.login_by_sessionid(sessionid)
        user_info = cl.account_info()

        device_settings = cl.get_settings()
        session_id = os.urandom(16).hex()

        user_sessions[session_id] = {
            "sessionid": sessionid,
            "device_settings": device_settings,
            "user_id": user_info.pk,
            "username": user_info.username,
            "non_followers": []
        }

        upsert_user_on_login(session_id, user_info.pk, user_info.username)

        logger.info("Login OK: @%s", user_info.username)
        return jsonify({"success": True, "session_id": session_id, "username": user_info.username})

    except LoginRequired:
        return jsonify({"success": False, "error": "session_expired"}), 401
    except ClientError as e:
        logger.exception("Instagram ClientError during login")
        return jsonify({"success": False, "error": f"ClientError: {e}"}), 400
    except Exception as e:
        logger.exception("Unexpected error during login")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
