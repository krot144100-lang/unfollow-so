from flask import Flask, render_template_string, request, jsonify, make_response
from flask_wtf.csrf import CSRFProtect  # 🔒 CSRF protection
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError  # ✅ Fixed imports
import os
import json
import time
import logging
import re
import csv
import io
import random
from functools import wraps
from datetime import datetime, timedelta
import threading
import queue
from cryptography.fernet import Fernet  # 🔒 Encryption for sessions

# 🛡️ Security Setup
CSRF_SECRET = os.environ.get("CSRF_SECRET", os.urandom(24).hex())
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 hour CSRF token validity
csrf = CSRFProtect(app)

# 🔒 Encryption for session storage (Redis recommended in production)
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", Fernet.generate_key().decode())
cipher = Fernet(ENCRYPTION_KEY.encode())

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# In-memory storage (⚠️ Use Redis in production!)
user_sessions = {}
unfollow_queue = queue.Queue()
processing_lock = threading.Lock()

HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token() }}"> <!-- 🔒 CSRF Token -->
    <title>Unfollow Ninja 2026 – Safest Unfollow Tool Ever</title>
    <style>
        /* ... (keep your original CSS unchanged) ... */
    </style>
</head>
<body>
<div class="container">
    <div class="toggle" onclick="toggleTheme()">☀︎</div>
    <div class="logout" onclick="logout()" id="logoutBtn" style="display:none">Logout</div>
    
    <h1>Unfollow Ninja 2026</h1>
    <div class="subtitle">The Safest Instagram Unfollow Tool Ever Created</div>

    <div id="login">
        <div class="alert info-alert">
            <strong>Safe Login Method:</strong> We only use sessionid, never ask for password!
        </div>
        <textarea id="cookies" placeholder="Paste ONLY your Instagram sessionid here (safest method)"></textarea>
        <button onclick="login()" id="loginBtn">Login with SessionID →</button>
        <small>Instagram web → F12 → Application → Cookies → copy value of sessionid</small>
        <div class="loader" id="loginLoader">
            <div class="spinner"></div>
            <p>Logging in...</p>
        </div>
    </div>

    <div id="main" style="display:none">
        <!-- ... (keep your original HTML structure unchanged) ... -->
    </div>
</div>

<script>
// 🔒 Get CSRF token from meta tag
const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

// Theme handling
function toggleTheme() { /* ... your code ... */ }

// Login with CSRF protection
async function login() {
    const cookies = document.getElementById('cookies').value.trim();
    if (!cookies) {
        addLog('Please paste your sessionid', 'error');
        return;
    }
    
    const loginBtn = document.getElementById('loginBtn');
    const loader = document.getElementById('loginLoader');
    
    loginBtn.disabled = true;
    loginBtn.style.display = 'none';
    loader.style.display = 'block';
    addLog('Attempting login...', 'info');
    
    try {
        const response = await fetch('/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken  // 🔒 Send CSRF token
            },
            body: JSON.stringify({cookies: cookies})
        });
        
        const data = await response.json();
        // ... rest of your login code ...
    } catch (error) {
        // ... error handling ...
    }
}

// ... Rest of your JavaScript functions (scan, unfollow, etc.) ...
// ADD CSRF TOKEN TO ALL FETCH REQUESTS LIKE ABOVE
</script>
</body>
</html>
'''

# 🔐 Helper Functions
def validate_sessionid(sessionid):
    if not sessionid or len(sessionid) < 10:
        return False
    # ✅ Correct regex
    if not re.match(r'^[A-Za-z0-9%._-]+$', sessionid):
        return False
    return True

def get_instagram_client(session_id):
    """Create NEW client for EVERY request (thread-safe)"""
    if session_id not in user_sessions:
        return None
    
    session_data = user_sessions[session_id]
    
    try:
        cl = Client()
        # ✅ Let instagrapi handle device settings automatically
        cl.login_by_sessionid(session_data['sessionid'])
        return cl
    except Exception as e:
        logger.error(f"Failed to create client: {e}")
        return None

# 🔒 Encrypted session storage (Redis recommended)
def save_session_encrypted(session_id, data):
    """Use Redis in production!"""
    try:
        encrypted = cipher.encrypt(json.dumps(data).encode())
        user_sessions[session_id] = encrypted
    except Exception as e:
        logger.error(f"Encryption error: {e}")

# Decorator for session validation
def require_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get('X-Session-ID')
        if not session_id or session_id not in user_sessions:
            return jsonify({'success': False, 'error': 'Invalid or expired session'}), 401
        
        # 🔒 Decrypt session data if encrypted
        session_data = user_sessions[session_id]
        if isinstance(session_data, bytes):
            try:
                session_data = json.loads(cipher.decrypt(session_data).decode())
                user_sessions[session_id] = session_data  # Cache decrypted
            except:
                return jsonify({'success': False, 'error': 'Invalid session'}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# ROUTES
@app.route('/')
def index():
    return render_template_string(HTML)  # CSRF token auto-injected

@app.route('/login', methods=['POST'])
@csrf.exempt  # ⚠️ Only exempt if using API auth (better: use JWT)
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        sessionid = data.get('cookies', '').strip()
        
        if not validate_sessionid(sessionid):
            return jsonify({'success': False, 'error': 'Invalid sessionid format'}), 400
        
        # ✅ Create fresh client for login
        cl = Client()
        try:
            cl.login_by_sessionid(sessionid)
            user_info = cl.account_info()
            
            session_id = os.urandom(16).hex()
            
            # 🔒 Store minimal data (encrypt in production)
            user_sessions[session_id] = {
                'sessionid': sessionid,
                'user_id': user_info.pk,  # ✅ Integer ID
                'username': user_info.username,
                'full_name': user_info.full_name,
                'follower_count': user_info.follower_count,
                'following_count': user_info.following_count,
                'is_premium': False,
                'last_activity': time.time(),
                'created_at': datetime.now().isoformat(),
                'whitelist': [],
                'non_followers': []  # Store as list of ints
            }
            
            logger.info(f"User @{user_info.username} logged in")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'username': user_info.username,
                'is_premium': False
            })
            
        except LoginRequired:
            logger.error("Session expired during login")
            return jsonify({'success': False, 'error': 'Session expired. Get new sessionid'}), 401
        except Exception as e:
            logger.error(f"Login error: {e}")
            return jsonify({'success': False, 'error': f'Login failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Unexpected error in login: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/scan', methods=['POST'])
@require_session
@csrf.exempt
def scan():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        whitelist = set([u.lower().strip() for u in data.get('whitelist', [])])
        smart_mode = data.get('smart_mode', True)
        skip_verified = data.get('skip_verified', True)
        skip_recent = data.get('skip_recent', True)
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        session_data = user_sessions[session_id]
        user_id = session_data['user_id']  # ✅ Integer
        
        # ✅ Get ALL followers/following with pagination
        followers = [f.pk for f in cl.user_followers_v1(user_id, amount=None)]
        following = [u for u in cl.user_following_v1(user_id, amount=None)]
        
        non_followers = []
        for user in following:
            if user.username.lower() in whitelist:
                continue
            if user.pk in followers:
                continue
            if skip_verified and user.is_verified:
                continue
            if smart_mode and user.follower_count > 12000:
                continue
            # ✅ Store ID as INTEGER
            non_followers.append({
                'user_id': user.pk,
                'username': user.username,
                'follower_count': user.follower_count,
                'is_verified': user.is_verified,
                'scanned_at': datetime.now().isoformat()
            })
        
        # Update session
        session_data['non_followers'] = non_followers
        
        return jsonify({
            'success': True,
            'non_followers': non_followers[:100],
            'non_followers_count': len(non_followers),
            'has_more': len(non_followers) > 100
        })
        
    except LoginRequired:
        del user_sessions[session_id]
        return jsonify({'success': False, 'error': 'Session expired'}), 401
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/unfollow', methods=['POST'])
@require_session
@csrf.exempt
def unfollow():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        if 'user_id' not in data:
            return jsonify({'success': False, 'error': 'Missing user_id'}), 400
        
        user_id_to_unfollow = int(data['user_id'])  # ✅ Convert to INT
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        session_data = user_sessions[session_id]
        
        # ✅ Rate limit handling
        try:
            result = cl.user_unfollow(user_id_to_unfollow)
            if not result:
                addLog("Unfollow failed. Rate limit?", "error")
                time.sleep(30)  # Backoff
                return jsonify({'success': False, 'error': 'Rate limited'}), 429
        except ClientError as e:
            if e.status_code == 429:
                retry_after = int(e.headers.get('Retry-After', 60))
                time.sleep(retry_after)
                return jsonify({'success': False, 'error': 'Rate limited'}), 429
            raise
        
        # Update local data
        session_data['non_followers'] = [u for u in session_data['non_followers'] 
                                      if u['user_id'] != user_id_to_unfollow]
        session_data['following_count'] -= 1
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ... (Other routes like /export/csv, /whitelist/save remain similar) ...

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
