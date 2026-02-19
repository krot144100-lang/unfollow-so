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
# 🛡️ CONFIGURATION & SECURITY
# ---------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(24).hex())
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
app.config["WTF_CSRF_HEADERS"] = ["X-CSRF-Token"]
csrf = CSRFProtect(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Runtime in-memory session store (resets on restart)
user_sessions = {}

# ---------------------------------------------------------
# 💾 DB (SQLite)
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

@app.before_request
def _db_bootstrap():
    init_db()

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
        logger.info(f"DB: created user @{ig_username} with {FREE_CREDITS} free credits")
    else:
        cur.execute("""
            UPDATE users
            SET ig_user_id=?, ig_username=?, updated_at=?
            WHERE session_id=?
        """, (str(ig_user_id), ig_username, ts, session_id))
        logger.info(f"DB: updated user @{ig_username}")

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
# 🔧 HELPERS
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
        cl.account_info()  # validate session
        return cl
    except Exception as e:
        logger.error(f"Client creation failed: {e}")
        return None

def validate_sessionid(sessionid):
    if not sessionid or len(sessionid) < 5:
        return False
    if not re.match(r"^[A-Za-z0-9%._-]+$", sessionid):
        return False
    return True

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
# 🖥️ DARK UI
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
input:focus, textarea:focus{border-color:var(--accent);box-shadow:0 0 0 2px rgba(255,0,128,.2)}

button.action-btn{width:100%;padding:16px;background:#fff;color:#000;border:none;border-radius:12px;font-size:15px;font-weight:900;cursor:pointer;transition:.2s;text-transform:uppercase;letter-spacing:1px}
button.action-btn:hover{transform:scale(1.01);background:#f0f0f0}
button.action-btn:disabled{opacity:.55;cursor:wait;transform:none}
button.secondary{background:rgba(255,255,255,.08);color:#fff;border:1px solid rgba(255,255,255,.12);text-transform:none;letter-spacing:0;font-weight:800}
button.secondary:hover{background:rgba(255,255,255,.10)}

hr{border:0;border-top:1px solid #1f1f1f;margin:16px 0}
.row{display:flex;gap:10px}
.row > *{flex:1}

#results{margin-top:14px;text-align:left}
.user-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 12px;border-radius:14px;border:1px solid #222;background:#0b0b0b;margin-top:10px}
.user-meta{min-width:0}
.user-meta strong{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.user-meta .sub{font-size:12px;color:#9aa0aa;margin-top:2px}
.btn-danger{background:rgba(255,0,128,.14);border:1px solid rgba(255,0,128,.35);color:#fff;padding:10px 12px;border-radius:12px;font-weight:900;cursor:pointer;transition:.2s}
.btn-danger:hover{background:rgba(255,0,128,.22)}
.btn-danger:disabled{opacity:.6;cursor:wait}

.small{font-size:12px;color:#9aa0aa;line-height:1.4;margin-top:10px}
.log{margin-top:14px;background:#0b0b0b;border:1px solid #222;border-radius:16px;padding:12px;text-align:left;font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#bfe3c6;max-height:180px;overflow:auto}

.pay-big{background:linear-gradient(135deg,#ff0080,#ff4081);color:#fff;padding:18px 16px;border-radius:18px;text-decoration:none;display:block;margin:22px auto 8px;font-weight:900;font-size:18px;box-shadow:0 10px 30px rgba(255,0,128,.3);transition:transform .2s;cursor:pointer;border:1px solid rgba(255,255,255,.1)}
.pay-big:hover{transform:scale(1.01);box-shadow:0 15px 40px rgba(255,0,128,.45)}
.pay-sub{font-size:12px;opacity:.85;font-weight:normal;margin-top:6px;display:block}

/* MODAL */
.modal-overlay{
  position:fixed; top:0; left:0;
  width:100%; height:100%;
  background:rgba(0,0,0,.8);
  backdrop-filter:blur(5px);
  z-index:999;
  display:flex;
  justify-content:center;
  align-items:center;
  opacity:0;
  visibility:hidden;
  transition:.25s;
  pointer-events:none;
}
.modal-overlay.active{
  opacity:1;
  visibility:visible;
  pointer-events:auto;
}
.modal-box{background:#141414;padding:22px;border-radius:22px;width:92%;max-width:420px;position:relative;border:1px solid #333;text-align:left;transform:translateY(16px);transition:.25s}
.modal-overlay.active .modal-box{transform:translateY(0)}
.close-btn{position:absolute;top:12px;right:16px;font-size:28px;cursor:pointer;color:#666}
.close-btn:hover{color:#fff}
.crypto-box{background:#000;padding:12px;border:1px dashed #444;border-radius:12px;margin-top:10px;font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#bbb;word-break:break-all;text-align:center;transition:.2s; flex: 1}
.crypto-box:hover{border-color:#ff0080;color:#fff;background:#0a0a0a}
.toast{margin-top:10px;color:#bfe3c6;font-size:12px}
.hidden{display:none !important;}
.warn{margin-top:10px;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,93,93,.35);background:rgba(255,93,93,.08);color:#ffd2d2;font-size:12px;line-height:1.35}

.copy-btn{
  width:auto;
  padding:12px 12px;
  border-radius:12px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.08);
  color:#fff;
  font-weight:900;
  cursor:pointer;
  transition:.2s;
  white-space:nowrap;
}
.copy-btn:hover{background:rgba(255,255,255,.10)}
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
    <textarea id="sessionid" placeholder="Paste Instagram sessionid cookie here..."></textarea>
    <button onclick="login()" id="loginBtn" class="action-btn">LOGIN</button>
    <div class="small">
      We never ask for passwords. You get <b>100</b> free unfollows per account. Then upgrade.
    </div>
    <hr>
  </div>

  <div id="appBox" class="hidden">
    <div class="row" style="margin-bottom:10px">
      <button class="action-btn secondary" onclick="scan()" id="scanBtn">Scan non-followers</button>
      <button class="action-btn secondary" onclick="logoutLocal()" id="logoutBtn">Sign out</button>
    </div>

    <div class="small" id="scanInfo"></div>
    <div id="results"></div>

    <a class="pay-big" onclick="openModal()">
      💳 GET PAID PLAN
      <span class="pay-sub">Pay USDT (TRC20) • Submit TXID • Manual activation</span>
    </a>
  </div>

  <div class="log" id="logs"><div>> System ready.</div></div>
</div>

<div id="paymentModal" class="modal-overlay">
  <div class="modal-box">
    <span class="close-btn" onclick="closeModal()">&times;</span>

    <h2 style="margin:0 0 6px 0;color:#fff">Activate plan</h2>
    <div style="color:#9aa0aa;font-size:13px;line-height:1.5">
      Send USDT on <b>TRC20</b>, then paste your TXID. Activation is manual.
    </div>

    <div class="warn"><b>TRC20 only.</b> Sending from other networks may result in loss.</div>

    <div style="margin-top:12px;color:#fff;font-weight:900">Send to address:</div>

    <div class="row" style="align-items:stretch; margin-top:10px">
      <div class="crypto-box" id="addrBox">{{ pay_addr if pay_addr else "SET PAYMENT_ADDRESS_TRC20 in env" }}</div>
      <button class="copy-btn" onclick="copyAddress()">Copy</button>
    </div>

    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="action-btn secondary" style="padding:12px;font-size:13px" onclick="selectPlan('starter')">$5 Starter</button>
      <button class="action-btn secondary" style="padding:12px;font-size:13px" onclick="selectPlan('lifetime')">$9 Lifetime</button>
    </div>

    <div class="small" id="planHint" style="margin-top:8px">
      Selected: STARTER — expected amount: <b>5 USDT</b> (TRC20)
    </div>

    <input id="txid" placeholder="Paste TXID here..." style="margin-top:12px" />

    <div class="row" style="margin-top:10px">
      <button class="action-btn secondary" style="padding:12px;font-size:13px" onclick="openTronScan()">
        OPEN TRONSCAN
      </button>
      <button class="action-btn" style="padding:12px;font-size:13px" onclick="submitTxid()">
        SUBMIT TXID
      </button>
    </div>

    <div id="payStatus" class="toast"></div>

    <div class="small" style="margin-top:10px; text-align:left;">
      <div style="font-weight:900;color:#fff;margin-bottom:6px;">How to find TXID</div>
      <div style="opacity:.9; line-height:1.5;">
        • <b>Trust Wallet:</b> USDT (TRC20) → History → open transfer → copy <b>TxID / Hash</b>.<br>
        • <b>Bybit:</b> Assets → History → select transfer → copy <b>TxID</b>.<br>
        • Make sure it’s <b>USDT on TRC20</b> and recipient matches the address above.
      </div>
    </div>

    <div class="small" style="margin-top:10px">
      Check your request status:
      <button class="secondary"
        style="padding:8px 10px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.08);color:#fff;cursor:pointer"
        onclick="loadMyRequests()">Refresh status</button>
    </div>

    <div id="myReq" class="small" style="margin-top:8px"></div>
  </div>
</div>

<script>
let currentSessionId = '';
let selectedPlan = "starter";
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
const modal = document.getElementById("paymentModal");
const addr = `{{ pay_addr if pay_addr else "" }}`;

function setPill(id, text){ const el = document.getElementById(id); if(el) el.textContent = text; }
function addLog(msg){
  const logs = document.getElementById('logs');
  const time = new Date().toLocaleTimeString();
  logs.innerHTML += `<div><span style="opacity:0.6">[${time}]</span> ${msg}</div>`;
  logs.scrollTop = logs.scrollHeight;
}

async function refreshMe(){
  if(!currentSessionId) return;
  try{
    const res = await fetch('/api/me', { headers: { 'X-Session-ID': currentSessionId }});
    const data = await res.json();
    if(data.ok){
      setPill('quotaState', `Plan: ${data.plan} • Credits: ${data.credits}`);
    }
  }catch(e){}
}

async function login(){
  const s = document.getElementById('sessionid').value.trim();
  if(!s) return alert('Paste sessionid');

  const btn = document.getElementById('loginBtn');
  btn.disabled = true;
  btn.textContent = 'VERIFYING...';

  try{
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type':'application/json', 'X-CSRF-Token': csrfToken },
      body: JSON.stringify({ cookies: s })
    });
    const data = await res.json();

    if(!data.success){
      addLog('Login failed: ' + (data.error || 'unknown'));
      return;
    }

    currentSessionId = data.session_id;
    setPill('authState', 'Signed in: @' + data.username);

    document.getElementById('loginBox').classList.add('hidden');
    document.getElementById('appBox').classList.remove('hidden');

    addLog('Login OK: @' + data.username);
    await refreshMe();
  }catch(e){
    addLog('Network error during login');
  } finally {
    btn.disabled = false;
    btn.textContent = 'LOGIN';
  }
}

async function scan(){
  if(!currentSessionId) return alert('Login first');

  const btn = document.getElementById('scanBtn');
  btn.disabled = true;
  btn.textContent = 'SCANNING...';

  try{
    const res = await fetch('/scan', {
      method: 'POST',
      headers: {
        'Content-Type':'application/json',
        'X-CSRF-Token': csrfToken,
        'X-Session-ID': currentSessionId
      },
      body: JSON.stringify({ smart_mode: true })
    });
    const data = await res.json();

    if(!data.success){
      addLog('Scan failed: ' + (data.error || 'unknown'));
      return;
    }

    addLog(`Scan complete. Non-followers: ${data.count}`);
    document.getElementById('scanInfo').textContent = `Found ${data.count} non-followers (showing up to ${data.non_followers.length}).`;
    renderList(data.non_followers || []);
  }catch(e){
    addLog('Network error during scan');
  }finally{
    btn.disabled = false;
    btn.textContent = 'Scan non-followers';
    await refreshMe();
  }
}

function renderList(users){
  const root = document.getElementById('results');
  root.innerHTML = '';
  if(!users.length){
    root.innerHTML = `<div class="small" style="margin-top:10px">Everyone follows you back.</div>`;
    return;
  }

  users.forEach(u => {
    const row = document.createElement('div');
    row.className = 'user-row';
    row.innerHTML = `
      <div class="user-meta">
        <strong>@${u.username}</strong>
        <div class="sub">${u.follower_count} followers</div>
      </div>
      <button class="btn-danger" onclick="unfollow('${u.user_id}', this)">UNFOLLOW</button>
    `;
    root.appendChild(row);
  });
}

async function unfollow(userId, btn){
  btn.disabled = true;
  btn.textContent = '...';

  try{
    const res = await fetch('/unfollow', {
      method: 'POST',
      headers: {
        'Content-Type':'application/json',
        'X-CSRF-Token': csrfToken,
        'X-Session-ID': currentSessionId
      },
      body: JSON.stringify({ user_id: userId })
    });

    if(res.status === 402){
      addLog('No credits left. Payment required.');
      btn.textContent = 'LOCKED';
      openModal();
      await refreshMe();
      return;
    }

    if(res.status === 429){
      addLog('Rate limited. Try later.');
      btn.textContent = 'LIMIT';
      return;
    }

    const data = await res.json();
    if(!data.success){
      addLog('Unfollow failed: ' + (data.error || 'unknown'));
      btn.disabled = false;
      btn.textContent = 'RETRY';
      return;
    }

    addLog('Unfollowed. Credits updated.');
    btn.textContent = 'DONE';
    btn.closest('.user-row').style.opacity = '0.55';

    await refreshMe();
  }catch(e){
    addLog('Network error during unfollow');
    btn.disabled = false;
    btn.textContent = 'RETRY';
  }
}

function logoutLocal(){
  currentSessionId = '';
  document.getElementById('sessionid').value = '';
  document.getElementById('appBox').classList.add('hidden');
  document.getElementById('loginBox').classList.remove('hidden');
  setPill('authState', 'Not signed in');
  setPill('quotaState', 'Plan: — • Credits: —');
  document.getElementById('results').innerHTML = '';
  document.getElementById('scanInfo').textContent = '';
  addLog('Signed out (local).');
}

/* Modal */
function openModal(){
  modal.classList.add("active");
  document.getElementById("payStatus").textContent = "";
  document.getElementById("myReq").textContent = "";
  loadMyRequests();
}
function closeModal(){ modal.classList.remove("active"); }
modal.addEventListener("click", (e) => { if(e.target === modal) closeModal(); });

function copyAddress(){
  if(!addr){
    alert("Payment address not configured on server.");
    return;
  }
  navigator.clipboard.writeText(addr).then(() => {
    document.getElementById("payStatus").textContent = "Address copied.";
    setTimeout(() => document.getElementById("payStatus").textContent = "", 1200);
  }).catch(() => prompt("Copy address:", addr));
}

function selectPlan(p){
  selectedPlan = p;
  const hint = document.getElementById("planHint");
  if (p === "starter") hint.innerHTML = "Selected: STARTER — expected amount: <b>5 USDT</b> (TRC20)";
  else hint.innerHTML = "Selected: LIFETIME — expected amount: <b>9 USDT</b> (TRC20)";
}

function openTronScan(){
  const txid = document.getElementById("txid").value.trim();
  if(txid){
    window.open("https://tronscan.org/#/transaction/" + txid, "_blank");
  } else {
    window.open("https://tronscan.org/", "_blank");
  }
}

async function submitTxid(){
  if(!currentSessionId){ alert("Login first"); return; }
  if(!addr){
    document.getElementById("payStatus").textContent = "Server missing PAYMENT_ADDRESS_TRC20 env.";
    return;
  }
  const txid = document.getElementById("txid").value.trim();
  if(!txid){
    document.getElementById("payStatus").textContent = "Paste TXID first.";
    return;
  }

  document.getElementById("payStatus").textContent = "Submitting…";

  try{
    const res = await fetch("/api/payment/submit-txid", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        "X-Session-ID": currentSessionId
      },
      body: JSON.stringify({ plan: selectedPlan, txid })
    });
    const j = await res.json();
    if(!j.ok){
      document.getElementById("payStatus").textContent = "Error: " + (j.error || res.status);
      return;
    }
    document.getElementById("payStatus").textContent = "Submitted. Status: pending (manual review).";
    document.getElementById("txid").value = "";
    await loadMyRequests();
  }catch(e){
    document.getElementById("payStatus").textContent = "Network error";
  }
}

async function loadMyRequests(){
  if(!currentSessionId) return;
  try{
    const res = await fetch("/api/payment/my-requests", { headers: { "X-Session-ID": currentSessionId }});
    const j = await res.json();
    if(!j.ok){
      document.getElementById("myReq").textContent = "";
      return;
    }
    const items = j.items || [];
    if(!items.length){
      document.getElementById("myReq").textContent = "No payment requests yet.";
      return;
    }
    const top = items[0];
    document.getElementById("myReq").textContent = `Latest: ${top.plan.toUpperCase()} • ${top.status} • TXID: ${top.txid.slice(0,10)}…`;
    if(top.status === "approved"){
      await refreshMe();
    }
  }catch(e){}
}

// default hint
selectPlan("starter");
</script>
</body>
</html>
"""

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
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        sessionid = data.get("cookies", "").strip()
        if not validate_sessionid(sessionid):
            return jsonify({"success": False, "error": "Invalid sessionid format"}), 400

        cl = Client()
        try:
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

            logger.info(f"Login: @{user_info.username}")
            return jsonify({"success": True, "session_id": session_id, "username": user_info.username})

        except LoginRequired:
            return jsonify({"success": False, "error": "Session expired. Get a fresh cookie."}), 401
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return jsonify({"success": False, "error": "Login failed. Check server logs."}), 500

    except Exception as e:
        logger.error(f"System error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

@app.route("/api/me", methods=["GET"])
@require_session
def api_me():
    session_id = request.headers.get("X-Session-ID")
    u = get_user_by_session(session_id)
    if not u:
        return jsonify({"ok": False, "error": "no_user"}), 404
    return jsonify({
        "ok": True,
        "plan": u["plan"],
        "credits": int(u["credits"]),
        "ig_username": u["ig_username"]
    })

@app.route("/api/payment/submit-txid", methods=["POST"])
@require_session
def submit_txid():
    session_id = request.headers.get("X-Session-ID")
    data = request.get_json() or {}
    plan = (data.get("plan") or "").strip()
    txid = (data.get("txid") or "").strip()

    if plan not in ("starter", "lifetime"):
        return jsonify({"ok": False, "error": "invalid_plan"}), 400
    if not validate_txid(txid):
        return jsonify({"ok": False, "error": "invalid_txid"}), 400

    conn = db()
    cur = conn.cursor()
    ts = now_iso()

    cur.execute("SELECT id, status FROM payment_requests WHERE txid=? ORDER BY id DESC LIMIT 1", (txid,))
    existing = cur.fetchone()
    if existing:
        conn.close()
        return jsonify({"ok": False, "error": "txid_already_submitted"}), 409

    cur.execute("""
        INSERT INTO payment_requests(session_id, plan, txid, status, created_at, updated_at)
        VALUES(?,?,?,?,?,?)
    """, (session_id, plan, txid, "pending", ts, ts))
    conn.commit()
    conn.close()

    logger.info(f"Payment request submitted: plan={plan}, txid={txid}, session={session_id}")
    return jsonify({"ok": True, "status": "pending"})

@app.route("/api/payment/my-requests", methods=["GET"])
@require_session
def my_payment_requests():
    session_id = request.headers.get("X-Session-ID")
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, plan, txid, status, created_at, updated_at
        FROM payment_requests
        WHERE session_id=?
        ORDER BY id DESC
        LIMIT 10
    """, (session_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "items": rows})

@app.route("/scan", methods=["POST"])
@require_session
def scan():
    try:
        session_id = request.headers.get("X-Session-ID")
        data = request.get_json() or {}
        smart_mode = data.get("smart_mode", True)

        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({"success": False, "error": "Session expired"}), 401

        session_data = user_sessions[session_id]
        user_id = session_data["user_id"]

        try:
            followers = cl.user_followers_v1(user_id, amount=2000)
            following = cl.user_following_v1(user_id, amount=2000)

            followers_iter = followers.values() if isinstance(followers, dict) else followers
            following_iter = following.values() if isinstance(following, dict) else following

            followers_set = {str(u.pk) for u in followers_iter}
            following_list = list(following_iter)
        except Exception as e:
            return jsonify({"success": False, "error": f"Instagram API Error: {str(e)}"}), 500

        non_followers = []
        for user in following_list:
            if str(user.pk) not in followers_set:
                if smart_mode:
                    if getattr(user, "is_verified", False):
                        continue
                    if getattr(user, "follower_count", 0) > 50000:
                        continue

                non_followers.append({
                    "user_id": str(user.pk),
                    "username": user.username,
                    "follower_count": getattr(user, "follower_count", 0)
                })

        session_data["non_followers"] = non_followers

        return jsonify({
            "success": True,
            "non_followers": non_followers[:100],
            "count": len(non_followers)
        })

    except Exception as e:
        logger.error(f"Scan error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/unfollow", methods=["POST"])
@require_session
def unfollow():
    try:
        session_id = request.headers.get("X-Session-ID")
        data = request.get_json() or {}
        user_id = int(data.get("user_id", 0))

        u = get_user_by_session(session_id)
        allowed, reason = can_unfollow(u)
        if not allowed:
            return jsonify({
                "success": False,
                "error": "Payment required",
                "code": reason,
                "plan": (u["plan"] if u else None),
                "credits": (int(u["credits"]) if u else 0)
            }), 402

        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({"success": False, "error": "Session expired"}), 401

        try:
            cl.user_unfollow(user_id)

            if u and u["plan"] != "lifetime":
                spend_credit(session_id, target_id=str(user_id), delta=-1)

            session_data = user_sessions[session_id]
            session_data["non_followers"] = [
                x for x in session_data.get("non_followers", [])
                if str(x.get("user_id")) != str(user_id)
            ]

            updated = get_user_by_session(session_id)
            return jsonify({"success": True, "plan": updated["plan"], "credits": int(updated["credits"])})

        except ClientError as e:
            if getattr(e, "status_code", None) == 429:
                return jsonify({"success": False, "error": "Rate limit hit"}), 429
            return jsonify({"success": False, "error": "API Error"}), 500

    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# Admin: approve request by TXID after manual verification on tronscan
@app.route("/api/admin/approve-txid", methods=["POST"])
def admin_approve_txid():
    if not ADMIN_GRANT_KEY:
        return jsonify({"ok": False, "error": "admin_disabled"}), 403
    if request.headers.get("X-Admin-Key") != ADMIN_GRANT_KEY:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json() or {}
    txid = (data.get("txid") or "").strip()

    if not validate_txid(txid):
        return jsonify({"ok": False, "error": "invalid_txid"}), 400

    conn = db()
    cur = conn.cursor()
    ts = now_iso()

    cur.execute("SELECT * FROM payment_requests WHERE txid=? ORDER BY id DESC LIMIT 1", (txid,))
    req = cur.fetchone()
    if not req:
        conn.close()
        return jsonify({"ok": False, "error": "txid_not_found"}), 404
    if req["status"] == "approved":
        conn.close()
        return jsonify({"ok": True, "already": True}), 200

    session_id = req["session_id"]
    plan = req["plan"]

    # Apply benefits
    if plan == "starter":
        cur.execute("""
            UPDATE users
            SET credits = credits + ?, updated_at=?
            WHERE session_id=? AND plan != 'lifetime'
        """, (STARTER_PACK_CREDITS, ts, session_id))
    elif plan == "lifetime":
        cur.execute("UPDATE users SET plan='lifetime', updated_at=? WHERE session_id=?", (ts, session_id))

    cur.execute("UPDATE payment_requests SET status='approved', updated_at=? WHERE id=?", (ts, int(req["id"])))

    conn.commit()
    conn.close()
    logger.info(f"Approved TXID {txid} for session {session_id}, plan={plan}")
    return jsonify({"ok": True, "session_id": session_id, "plan": plan})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
