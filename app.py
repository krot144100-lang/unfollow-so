from flask import Flask, render_template_string, request, jsonify
from flask_wtf.csrf import CSRFProtect
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import os
import json
import time
import logging
import re
from functools import wraps
from datetime import datetime, timedelta

# ---------------------------------------------------------
# 🛡️ CONFIGURATION & SECURITY
# ---------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['WTF_CSRF_TIME_LIMIT'] = 3600
app.config['WTF_CSRF_HEADERS'] = ['X-CSRF-Token'] # Explicitly look for this header

csrf = CSRFProtect(app)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ⚠️ In-memory storage (Reset on restart). Use Redis for production.
user_sessions = {}

# ---------------------------------------------------------
# 🔧 HELPER FUNCTIONS
# ---------------------------------------------------------

def get_instagram_client(session_id):
    """
    Reconstructs the Instagrapi Client using saved device settings.
    CRITICAL: Using saved settings prevents Instagram from seeing
    a 'New Device' login on every request.
    """
    if session_id not in user_sessions:
        return None
    
    session_data = user_sessions[session_id]
    
    try:
        cl = Client()
        
        # ✅ Load specific device settings to prevent bans
        if 'device_settings' in session_data:
            cl.set_settings(session_data['device_settings'])
        
        # Login using the sessionid cookie
        cl.login_by_sessionid(session_data['sessionid'])
        return cl
    except Exception as e:
        logger.error(f"Failed to create client: {e}")
        return None

def validate_sessionid(sessionid):
    if not sessionid or len(sessionid) < 5:
        return False
    # Validate it only contains URL-safe characters
    if not re.match(r'^[A-Za-z0-9%._-]+$', sessionid):
        return False
    return True

def require_session(f):
    """Decorator to ensure user is logged in."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get('X-Session-ID')
        if not session_id or session_id not in user_sessions:
            return jsonify({'success': False, 'error': 'Invalid or expired session'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------
# 🖥️ FRONTEND TEMPLATE
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
        body.dark { --bg: #121212; --card: #1e1e1e; --text: #e0e0e0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; transition: 0.3s; }
        .container { max-width: 600px; margin: 0 auto; background: var(--card); padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1 { text-align: center; margin-bottom: 5px; }
        textarea { width: 100%; height: 80px; padding: 10px; margin: 10px 0; border: 1px solid #dbdbdb; border-radius: 6px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; }
        .btn-primary { background: var(--blue); color: white; }
        .btn-danger { background: var(--red); color: white; }
        .user-row { display: flex; align-items: center; justify-content: space-between; padding: 12px; border-bottom: 1px solid #dbdbdb; }
        .log-area { background: #000; color: #0f0; font-family: monospace; padding: 10px; border-radius: 6px; margin-top: 20px; max-height: 150px; overflow-y: auto; font-size: 12px; }
        .hidden { display: none; }
    </style>
</head>
<body>
<div class="container">
    <h1>Unfollow Ninja</h1>
    
    <!-- Login Section -->
    <div id="login-section">
        <p>Paste your <code>sessionid</code> cookie below:</p>
        <textarea id="cookies" placeholder="Warning: Do not share this ID with anyone else."></textarea>
        <button class="btn-primary" onclick="login()" id="loginBtn">Login</button>
    </div>

    <!-- Main Section -->
    <div id="main-section" class="hidden">
        <h3>Welcome, <span id="username-display"></span></h3>
        <button class="btn-primary" onclick="scan()" id="scanBtn">Scan Non-Followers</button>
        <div id="results-area"></div>
    </div>

    <!-- Logs -->
    <div class="log-area" id="logs"></div>
</div>

<script>
let currentSessionId = '';
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

function addLog(msg) {
    const logs = document.getElementById('logs');
    logs.innerHTML += `<div>> ${msg}</div>`;
    logs.scrollTop = logs.scrollHeight;
}

async function login() {
    const cookies = document.getElementById('cookies').value.trim();
    if(!cookies) return alert('Enter sessionid');
    
    document.getElementById('loginBtn').disabled = true;
    addLog('Logging in...');

    try {
        const res = await fetch('/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({cookies: cookies})
        });
        const data = await res.json();
        
        if(data.success) {
            currentSessionId = data.session_id;
            document.getElementById('username-display').innerText = '@' + data.username;
            document.getElementById('login-section').classList.add('hidden');
            document.getElementById('main-section').classList.remove('hidden');
            addLog('Login successful!');
        } else {
            addLog('Error: ' + data.error);
            document.getElementById('loginBtn').disabled = false;
        }
    } catch(e) {
        addLog('Network Error');
        document.getElementById('loginBtn').disabled = false;
    }
}

async function scan() {
    addLog('Scanning... This may take a moment.');
    document.getElementById('scanBtn').disabled = true;
    
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
            addLog(`Found ${data.count} non-followers.`);
            renderList(data.non_followers);
        } else {
            addLog('Scan Error: ' + data.error);
        }
    } catch(e) {
        addLog('Error during scan');
    }
    document.getElementById('scanBtn').disabled = false;
}

function renderList(users) {
    const container = document.getElementById('results-area');
    container.innerHTML = '';
    users.forEach(u => {
        const div = document.createElement('div');
        div.className = 'user-row';
        div.innerHTML = `
            <span><b>${u.username}</b> <small>(${u.follower_count} followers)</small></span>
            <button class="btn-danger" style="width:auto; padding:5px 15px;" onclick="unfollow('${u.user_id}', this)">Unfollow</button>
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
            addLog('Rate limit hit! Waiting 60s...');
            btn.innerText = 'Limit';
            return;
        }

        const data = await res.json();
        if(data.success) {
            addLog('Unfollowed user.');
            btn.parentElement.remove();
        } else {
            addLog('Error: ' + data.error);
            btn.innerText = 'Retry';
            btn.disabled = false;
        }
    } catch(e) {
        addLog('Network error');
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
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        sessionid = data.get('cookies', '').strip()
        
        if not validate_sessionid(sessionid):
            return jsonify({'success': False, 'error': 'Invalid sessionid format'}), 400
        
        # Initialize client
        cl = Client()
        try:
            cl.login_by_sessionid(sessionid)
            user_info = cl.account_info()
            
            # ✅ CRITICAL: Save device settings to reuse later
            device_settings = cl.get_settings()
            
            session_id = os.urandom(16).hex()
            
            user_sessions[session_id] = {
                'sessionid': sessionid,
                'device_settings': device_settings, # Saved here
                'user_id': user_info.pk,
                'username': user_info.username,
                'whitelist': [],
                'non_followers': []
            }
            
            logger.info(f"User @{user_info.username} logged in")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'username': user_info.username
            })
            
        except LoginRequired:
            return jsonify({'success': False, 'error': 'Session expired. Get new sessionid'}), 401
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return jsonify({'success': False, 'error': f'Login failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"System error in login: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/scan', methods=['POST'])
@require_session
def scan():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        smart_mode = data.get('smart_mode', True)
        whitelist = set([u.lower().strip() for u in data.get('whitelist', [])])
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        session_data = user_sessions[session_id]
        user_id = session_data['user_id']
        
        # ✅ Optimized Scan (Set for O(1) lookup)
        try:
            # Note: amount=2000 prevents timeouts on free servers. 
            # For larger accounts, you need background workers (Celery).
            followers_map = {str(f.pk) for f in cl.user_followers_v1(user_id, amount=2000)}
            following_list = cl.user_following_v1(user_id, amount=2000)
        except Exception as e:
             return jsonify({'success': False, 'error': f'Instagram API Error: {str(e)}'}), 500
        
        non_followers = []
        
        for user in following_list:
            if user.username.lower() in whitelist: continue
            
            # If user PK is NOT in followers set, they don't follow back
            if str(user.pk) not in followers_map:
                
                # Smart Mode Filters
                if smart_mode:
                    if user.is_verified: continue # Don't unfollow celebs
                    if user.follower_count > 50000: continue # Likely a page
                
                non_followers.append({
                    'user_id': str(user.pk), # String to ensure JS compatibility
                    'username': user.username,
                    'follower_count': user.follower_count
                })
        
        # Store in session
        session_data['non_followers'] = non_followers
        
        return jsonify({
            'success': True,
            'non_followers': non_followers[:100], # Send first 100
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
        
        if 'user_id' not in data:
            return jsonify({'success': False, 'error': 'Missing user_id'}), 400
        
        user_id_to_unfollow = int(data['user_id'])
        
        cl = get_instagram_client(session_id)
        
        try:
            cl.user_unfollow(user_id_to_unfollow)
            
            # Update local session data
            session_data = user_sessions[session_id]
            session_data['non_followers'] = [
                u for u in session_data['non_followers'] 
                if str(u['user_id']) != str(user_id_to_unfollow)
            ]
            
            return jsonify({'success': True})
            
        except ClientError as e:
            # Handle Instagram Rate Limits (429)
            if e.status_code == 429:
                return jsonify({'success': False, 'error': 'Rate limit'}), 429
            logger.error(f"Insta Error: {e}")
            return jsonify({'success': False, 'error': 'Instagram API Error'}), 500
        
    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
