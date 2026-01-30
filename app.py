from flask import Flask, render_template_string, request, jsonify
from flask_wtf.csrf import CSRFProtect
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import os
import logging
import re
from functools import wraps
import time

# ---------------------------------------------------------
# 🛡️ CONFIGURATION & SECURITY
# ---------------------------------------------------------
app = Flask(__name__)
# Security Key
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['WTF_CSRF_TIME_LIMIT'] = 3600
app.config['WTF_CSRF_HEADERS'] = ['X-CSRF-Token'] 

csrf = CSRFProtect(app)

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ⚠️ In-memory storage (Resets on server restart)
user_sessions = {}

# ---------------------------------------------------------
# 🔧 HELPER FUNCTIONS
# ---------------------------------------------------------

def get_instagram_client(session_id):
    """Reconstructs the client using SAVED device settings to prevent bans."""
    if session_id not in user_sessions:
        return None
    
    session_data = user_sessions[session_id]
    
    try:
        cl = Client()
        # ✅ Load specific device settings
        if 'device_settings' in session_data:
            cl.set_settings(session_data['device_settings'])
        
        cl.login_by_sessionid(session_data['sessionid'])
        return cl
    except Exception as e:
        logger.error(f"Client creation failed: {e}")
        return None

def validate_sessionid(sessionid):
    if not sessionid or len(sessionid) < 5:
        return False
    if not re.match(r'^[A-Za-z0-9%._-]+$', sessionid):
        return False
    return True

def require_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get('X-Session-ID')
        if not session_id or session_id not in user_sessions:
            return jsonify({'success': False, 'error': 'Invalid session'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------
# 🖥️ FRONTEND TEMPLATE (HTML + CSS + JS)
# ---------------------------------------------------------
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <title>Unfollow Ninja 2026</title>
    <style>
        :root { --bg: #f0f2f5; --card: #ffffff; --text: #1c1e21; --blue: #0095f6; --red: #ed4956; }
        body { font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; }
        
        .container { max-width: 600px; margin: 0 auto; background: var(--card); padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1 { text-align: center; color: #333; }
        
        /* Inputs & Buttons */
        textarea { width: 100%; height: 80px; padding: 10px; margin: 10px 0; border: 1px solid #dbdbdb; border-radius: 6px; box-sizing: border-box; font-family: monospace; }
        button { width: 100%; padding: 12px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; font-size: 16px; }
        .btn-primary { background: var(--blue); color: white; }
        .btn-primary:hover { background: #0081d6; }
        .btn-danger { background: var(--red); color: white; width: auto; padding: 5px 15px; font-size: 14px; }
        
        /* List Styling */
        .user-row { display: flex; align-items: center; justify-content: space-between; padding: 12px; border-bottom: 1px solid #dbdbdb; }
        .log-area { background: #1a1a1a; color: #00ff9d; font-family: monospace; padding: 15px; border-radius: 6px; margin-top: 20px; max-height: 200px; overflow-y: auto; font-size: 13px; }
        .hidden { display: none !important; }

        /* 🔥 THE PAY BUTTON */
        .pay-big {
            background: linear-gradient(135deg, #ff0080, #ff4081);
            color: white;
            padding: 28px 32px;
            border-radius: 20px;
            text-decoration: none;
            display: block;
            margin: 50px auto 30px;
            font-weight: bold;
            font-size: 22px;
            text-align: center;
            box-shadow: 0 10px 25px rgba(255, 0, 128, 0.4);
            transition: transform 0.2s;
            cursor: pointer;
        }
        .pay-big:hover { transform: scale(1.02); }

        /* 💰 PAYMENT MODAL STYLES */
        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); z-index: 1000; display: flex; justify-content: center; align-items: center; }
        .modal-box { background: white; padding: 30px; border-radius: 15px; width: 90%; max-width: 400px; position: relative; animation: slideUp 0.3s; }
        .close-btn { position: absolute; top: 15px; right: 20px; font-size: 24px; cursor: pointer; color: #999; }
        .crypto-box { background: #f8f9fa; padding: 15px; border: 1px solid #ddd; border-radius: 8px; margin-top: 10px; word-break: break-all; font-family: monospace; font-size: 14px; }
        @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    </style>
</head>
<body>

<div class="container">
    <h1>🥷 Unfollow Ninja</h1>

    <!-- Login Screen -->
    <div id="login-section">
        <div style="background:#eef; padding:10px; border-radius:8px; margin-bottom:15px; font-size:14px;">
            <strong>Safe Login:</strong> Paste your <code>sessionid</code> cookie below. We never ask for passwords.
        </div>
        <textarea id="cookies" placeholder="Paste sessionid here..."></textarea>
        <button class="btn-primary" onclick="login()" id="loginBtn">Login securely</button>
    </div>

    <!-- Main App Screen -->
    <div id="main-section" class="hidden">
        <h3 style="text-align:center">Welcome, <span id="username-display"></span></h3>
        
        <div style="display:flex; gap:10px; margin-bottom:20px;">
            <button class="btn-primary" onclick="scan()" id="scanBtn">🔍 Scan Non-Followers</button>
        </div>

        <div id="results-area"></div>
        
        <!-- The Upsell Button -->
        <a class="pay-big" onclick="openPaymentModal()">
            🚀 UNLOCK PREMIUM
        </a>
    </div>

    <!-- Logs Console -->
    <div class="log-area" id="logs">
        <div>> System ready. Waiting for login...</div>
    </div>
</div>

<!-- 💰 PAYMENT MODAL POPUP -->
<div id="paymentModal" class="modal-overlay hidden">
    <div class="modal-box">
        <span class="close-btn" onclick="closePaymentModal()">&times;</span>
        <h2 style="margin-top:0">🚀 Upgrade to Premium</h2>
        <p>Unlock unlimited scans and auto-unfollow bot.</p>
        
        <div style="margin-top:20px;">
            <strong>Option 1: Crypto (USDT/BTC)</strong>
            <div class="crypto-box">
                <!-- 👇 EDIT THIS ADDRESS 👇 -->
                0x123456789ABCDEF_YOUR_WALLET_ADDRESS
            </div>
            <p style="font-size:12px; color:#666;">Copy address above and send $10.</p>
        </div>

        <div style="margin-top:20px;">
            <strong>Option 2: PayPal</strong>
            <a href="https://paypal.me/" target="_blank" class="btn-primary" style="display:block; text-align:center; text-decoration:none; margin-top:5px;">
                Pay with PayPal
            </a>
        </div>

        <div style="text-align:center; margin-top:20px; font-size:12px; color:#888;">
            After payment, please email support@example.com
        </div>
    </div>
</div>

<script>
let currentSessionId = '';
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

// --- PAYMENT MODAL FUNCTIONS ---
function openPaymentModal() {
    document.getElementById('paymentModal').classList.remove('hidden');
}
function closePaymentModal() {
    document.getElementById('paymentModal').classList.add('hidden');
}
// Close if clicking outside the box
document.getElementById('paymentModal').addEventListener('click', function(e) {
    if (e.target === this) closePaymentModal();
});

// --- LOGIC FUNCTIONS ---
function addLog(msg) {
    const logs = document.getElementById('logs');
    const time = new Date().toLocaleTimeString();
    logs.innerHTML += `<div><span style="opacity:0.5">[${time}]</span> ${msg}</div>`;
    logs.scrollTop = logs.scrollHeight;
}

async function login() {
    const cookies = document.getElementById('cookies').value.trim();
    if(!cookies) return alert('Please enter sessionid');
    
    const btn = document.getElementById('loginBtn');
    btn.disabled = true;
    btn.innerText = 'Verifying...';
    
    try {
        const res = await fetch('/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({cookies: cookies})
        });
        const data = await res.json();
        
        if(data.success) {
            currentSessionId = data.session_id;
            document.getElementById('username-display').innerText = '@' + data.username;
            document.getElementById('login-section').classList.add('hidden');
            document.getElementById('main-section').classList.remove('hidden');
            addLog('Login successful! Device settings saved.');
        } else {
            addLog('❌ Error: ' + data.error);
            btn.innerText = 'Login securely';
            btn.disabled = false;
        }
    } catch(e) {
        addLog('❌ Network Error');
        btn.disabled = false;
    }
}

async function scan() {
    addLog('Scanning followers... (This takes 10-20 seconds)');
    const btn = document.getElementById('scanBtn');
    btn.disabled = true;
    
    try {
        const res = await fetch('/scan', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json', 
                'X-CSRF-Token': csrfToken,
                'X-Session-ID': currentSessionId
            },
            body: JSON.stringify({ smart_mode: true })
        });
        const data = await res.json();
        
        if(data.success) {
            addLog(`✅ Analysis complete. Found ${data.count} people who don't follow back.`);
            renderList(data.non_followers);
        } else {
            addLog('❌ Scan failed: ' + data.error);
        }
    } catch(e) {
        addLog('❌ Error during scan');
    }
    btn.disabled = false;
}

function renderList(users) {
    const container = document.getElementById('results-area');
    container.innerHTML = '';
    
    if(users.length === 0) {
        container.innerHTML = '<div style="text-align:center; padding:20px;">Everyone follows you back! 🎉</div>';
        return;
    }

    users.forEach(u => {
        const div = document.createElement('div');
        div.className = 'user-row';
        div.innerHTML = `
            <div>
                <strong>${u.username}</strong>
                <div style="font-size:12px; color:#666">${u.follower_count} followers</div>
            </div>
            <button class="btn-danger" onclick="unfollow('${u.user_id}', this)">Unfollow</button>
        `;
        container.appendChild(div);
    });
}

async function unfollow(userId, btn) {
    btn.disabled = true;
    btn.innerText = '...';
    
    try {
        const res = await fetch('/unfollow', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json', 
                'X-CSRF-Token': csrfToken,
                'X-Session-ID': currentSessionId
            },
            body: JSON.stringify({ user_id: userId })
        });
        
        if(res.status === 429) {
            addLog('⚠️ Rate limited. Waiting 60s...');
            btn.innerText = 'Wait';
            return;
        }

        const data = await res.json();
        if(data.success) {
            addLog('Unfollowed user.');
            btn.parentElement.style.opacity = '0.5';
            btn.innerText = 'Done';
        } else {
            addLog('❌ Error: ' + data.error);
            btn.innerText = 'Retry';
            btn.disabled = false;
        }
    } catch(e) {
        addLog('❌ Network error');
        btn.disabled = false;
    }
}
</script>
</body>
</html>
'''

# ---------------------------------------------------------
# 🛣️ ROUTES
# ---------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data: return jsonify({'success': False, 'error': 'No data'}), 400
        
        sessionid = data.get('cookies', '').strip()
        if not validate_sessionid(sessionid):
            return jsonify({'success': False, 'error': 'Invalid sessionid format'}), 400
        
        cl = Client()
        try:
            cl.login_by_sessionid(sessionid)
            user_info = cl.account_info()
            
            # ✅ SAVE DEVICE SETTINGS
            device_settings = cl.get_settings()
            
            session_id = os.urandom(16).hex()
            
            user_sessions[session_id] = {
                'sessionid': sessionid,
                'device_settings': device_settings,
                'user_id': user_info.pk,
                'username': user_info.username,
                'non_followers': []
            }
            
            logger.info(f"Login: @{user_info.username}")
            
            return jsonify({
                'success': True, 
                'session_id': session_id,
                'username': user_info.username
            })
            
        except LoginRequired:
            return jsonify({'success': False, 'error': 'Session expired. Get a fresh cookie.'}), 401
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return jsonify({'success': False, 'error': 'Login failed. Check server logs.'}), 500
            
    except Exception as e:
        logger.error(f"System error: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/scan', methods=['POST'])
@require_session
def scan():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        smart_mode = data.get('smart_mode', True)
        
        cl = get_instagram_client(session_id)
        if not cl: return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        session_data = user_sessions[session_id]
        user_id = session_data['user_id']
        
        try:
            # Note: Limiting to 2000 for standard web server timeout safety
            followers_set = {str(f.pk) for f in cl.user_followers_v1(user_id, amount=2000)}
            following_list = cl.user_following_v1(user_id, amount=2000)
        except Exception as e:
             return jsonify({'success': False, 'error': f'Instagram API Error: {str(e)}'}), 500
        
        non_followers = []
        
        for user in following_list:
            if str(user.pk) not in followers_set:
                if smart_mode:
                    if user.is_verified: continue 
                    if user.follower_count > 50000: continue 
                
                non_followers.append({
                    'user_id': str(user.pk),
                    'username': user.username,
                    'follower_count': user.follower_count
                })
        
        session_data['non_followers'] = non_followers
        
        return jsonify({
            'success': True,
            'non_followers': non_followers[:100],
            'count': len(non_followers)
        })
        
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/unfollow', methods=['POST'])
@require_session
def unfollow():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        user_id = int(data.get('user_id', 0))
        
        cl = get_instagram_client(session_id)
        
        try:
            cl.user_unfollow(user_id)
            session_data = user_sessions[session_id]
            session_data['non_followers'] = [u for u in session_data['non_followers'] if str(u['user_id']) != str(user_id)]
            return jsonify({'success': True})
            
        except ClientError as e:
            if e.status_code == 429:
                return jsonify({'success': False, 'error': 'Rate limit hit'}), 429
            return jsonify({'success': False, 'error': 'API Error'}), 500
        
    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
