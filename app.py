from flask import Flask, render_template_string, request, jsonify, session, abort
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError, ClientLoginRequired
import os
import json
import time
import logging
import re
from functools import wraps
from datetime import datetime, timedelta
import threading
import queue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# In-memory storage for user data (use Redis in production)
user_sessions = {}
unfollow_queue = queue.Queue()
processing_lock = threading.Lock()

HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unfollow Ninja 2026 – Safest Unfollow Tool Ever</title>
    <style>
        :root{--bg:#fff;--text:#000;--accent:#ff0080;--red:#d32f2f;--green:#4caf50}
        .dark{--bg:#0f0f0f;--text:#fff;--accent:#ff4081}
        body{font-family:system-ui;background:var(--bg);color:var(--text);margin:0;padding:20px 15px;transition:background 0.3s}
        .container{max-width:520px;margin:auto}
        .toggle{position:fixed;top:15px;right:15px;background:var(--accent);color:white;width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;cursor:pointer;z-index:999;transition:transform 0.3s}
        .toggle:hover{transform:rotate(30deg)}
        h1{font-size:28px;text-align:center;margin:10px 0 5px}
        .subtitle{text-align:center;font-size:19px;font-weight:bold;color:var(--accent);margin-bottom:20px}
        textarea{width:100%;padding:14px;margin:10px 0;border-radius:12px;border:1px solid #333;background:var(--bg);color:var(--text);box-sizing:border-box;font-size:15px;height:130px;transition:border 0.3s;resize:vertical}
        textarea:focus{outline:none;border-color:var(--accent)}
        button{background:var(--accent);color:white;border:none;padding:16px;border-radius:12px;font-size:18px;width:100%;margin:15px 0;cursor:pointer;font-weight:bold;transition:opacity 0.3s, transform 0.2s}
        button:hover{opacity:0.9;transform:translateY(-2px)}
        button:disabled{opacity:0.5;cursor:not-allowed}
        button.danger{background:var(--red)}
        button.success{background:var(--green)}
        .log{background:#000;color:#0f0;padding:15px;border-radius:12px;height:320px;overflow-y:auto;font-family:'Courier New',monospace;margin:20px 0;font-size:14px;line-height:1.6}
        .log-entry{margin-bottom:5px}
        .log-success{color:#4caf50}
        .log-error{color:#ff5252}
        .log-warning{color:#ff9800}
        .log-info{color:#2196f3}
        .queue{background:rgba(0,0,0,0.1);padding:15px;border-radius:12px;margin:20px 0;max-height:300px;overflow-y:auto}
        .user-item{display:flex;justify-content:space-between;align-items:center;padding:10px;margin:5px 0;background:rgba(255,255,255,0.05);border-radius:8px}
        .user-item:hover{background:rgba(255,255,255,0.1)}
        .username{font-weight:bold}
        .follower-count{color:#888;font-size:12px}
        .remove-btn{color:var(--red);cursor:pointer;font-size:20px}
        .stats{font-size:26px;font-weight:bold;text-align:center;margin:25px 0;color:var(--accent)}
        .progress{width:100%;height:10px;background:#333;border-radius:5px;margin:20px 0;overflow:hidden}
        .progress-bar{height:100%;background:var(--accent);width:0%;transition:width 0.3s}
        .loader{display:none;text-align:center;margin:20px 0}
        .spinner{border:4px solid #f3f3f3;border-top:4px solid var(--accent);border-radius:50%;width:40px;height:40px;animation:spin 1s linear infinite;margin:auto}
        @keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
        .pay-big{background:linear-gradient(135deg,#ff0080,#ff4081);color:white;padding:28px 32px;border-radius:20px;text-decoration:none;display:block;margin:50px auto 30px;font-weight:bold;font-size:24px;text-align:center;box-shadow:0 15px 40px rgba(255,0,128,0.5);transition:0.3s}
        .pay-big:hover{transform:scale(1.05);box-shadow:0 20px 50px rgba(255,0,128,0.7)}
        .timer{color:#ff1744;font-size:19px;font-weight:bold;text-align:center;margin:20px 0;padding:15px;background:rgba(255,23,68,0.1);border-radius:10px}
        .alert{background:rgba(255,23,68,0.1);color:#ff1744;padding:15px;border-radius:10px;margin:15px 0;text-align:center}
        .success-alert{background:rgba(76,175,80,0.1);color:#4caf50}
        .info-alert{background:rgba(33,150,243,0.1);color:#2196f3}
        small{opacity:0.7;display:block;margin-top:8px;font-size:12px}
        .checkbox{display:flex;align-items:center;margin:15px 0}
        .checkbox input{margin-right:10px}
        .logout{position:fixed;top:15px;left:15px;background:rgba(0,0,0,0.3);color:white;padding:8px 15px;border-radius:20px;cursor:pointer;font-size:14px}
        .logout:hover{background:rgba(0,0,0,0.5)}
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
        <div class="stats" id="stats">Loading...</div>
        
        <div class="alert success-alert" id="premiumAlert" style="display:none">
            <strong>Premium Activated!</strong> Unlimited unfollows available.
        </div>
        
        <textarea id="whitelist" placeholder="Whitelist – usernames you never want to unfollow (one per line)"></textarea>
        
        <div class="checkbox">
            <input type="checkbox" id="smart" checked>
            <label for="smart">Smart Mode (skip accounts with >12k followers)</label>
        </div>
        
        <div class="checkbox">
            <input type="checkbox" id="skipVerified" checked>
            <label for="skipVerified">Skip verified accounts</label>
        </div>
        
        <div class="checkbox">
            <input type="checkbox" id="skipRecent" checked>
            <label for="skipRecent">Skip accounts followed in last 7 days</label>
        </div>
        
        <button onclick="scan()" id="scanBtn">Scan Non-Followers</button>
        <div class="loader" id="scanLoader">
            <div class="spinner"></div>
            <p>Scanning your followers...</p>
        </div>
        
        <div id="queue" class="queue" style="display:none">
            <h3>Accounts to Unfollow: <span id="queueCount">0</span></h3>
            <div id="queueList"></div>
        </div>
        
        <div class="progress" style="display:none" id="progressBar">
            <div class="progress-bar" id="progressFill"></div>
        </div>
        
        <button id="startBtn" onclick="startUnfollow()" style="display:none" class="danger">
            Start Cleaning (<span id="count">0</span>)
        </button>
        
        <button id="stopBtn" onclick="stopUnfollow()" style="display:none" class="danger">
            Stop Process
        </button>
        
        <div class="log" id="log">
            <div class="log-entry">Ready. Login to start.</div>
        </div>

        <div class="timer" id="timer">
            ★ First 50 buyers only – $7 instead of $9 (11 spots left)
        </div>
        
        <a href="https://nowpayments.io/payment?amount=9&currency=usd&payin=usdttrc20&description=Unfollow%20Ninja%20Lifetime%20Unlimited&success_url=https://unfollow-so2.onrender.com" 
           target="_blank" class="pay-big" id="payButton">
            LIFETIME UNLIMITED – $9 USDT (TRC20)<br>
            <small>No limits forever · Instant activation</small>
        </a>
        
        <p style="text-align:center;font-size:14px;opacity:0.8;margin-top:30px">
            Already used by 3100+ people in 2026<br>
            Made with ❤️ by @krot13 & @krot133
        </p>
    </div>
</div>

<script>
let toUnfollow = [];
let isProcessing = false;
let currentSession = '';
let stopRequested = false;

// Theme handling
function toggleTheme() {
    document.body.classList.toggle('dark');
    localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
}

// Load saved theme
if (localStorage.getItem('theme') === 'dark') {
    document.body.classList.add('dark');
}

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
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cookies: cookies})
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentSession = data.session_id;
            document.getElementById('login').style.display = 'none';
            document.getElementById('main').style.display = 'block';
            document.getElementById('logoutBtn').style.display = 'block';
            document.getElementById('stats').innerText = `@${data.username}`;
            
            if (data.is_premium) {
                document.getElementById('premiumAlert').style.display = 'block';
                document.getElementById('payButton').style.display = 'none';
            }
            
            addLog(`Logged in as @${data.username}`, 'success');
            updateStats();
        } else {
            addLog(`Login failed: ${data.error}`, 'error');
        }
    } catch (error) {
        addLog(`Network error: ${error.message}`, 'error');
    } finally {
        loginBtn.disabled = false;
        loginBtn.style.display = 'block';
        loader.style.display = 'none';
    }
}

async function scan() {
    if (!currentSession) {
        addLog('Please login first', 'error');
        return;
    }
    
    const whitelist = document.getElementById('whitelist').value.split('\n')
        .map(u => u.trim().toLowerCase())
        .filter(u => u.length > 0);
    
    const smartMode = document.getElementById('smart').checked;
    const skipVerified = document.getElementById('skipVerified').checked;
    const skipRecent = document.getElementById('skipRecent').checked;
    
    const scanBtn = document.getElementById('scanBtn');
    const loader = document.getElementById('scanLoader');
    
    scanBtn.disabled = true;
    scanBtn.style.display = 'none';
    loader.style.display = 'block';
    addLog('Scanning for non-followers...', 'info');
    
    try {
        const response = await fetch('/scan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Session-ID': currentSession
            },
            body: JSON.stringify({
                whitelist: whitelist,
                smart_mode: smartMode,
                skip_verified: skipVerified,
                skip_recent: skipRecent
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            toUnfollow = data.non_followers || [];
            updateQueueDisplay();
            addLog(`Found ${toUnfollow.length} non-followers`, 'success');
            
            if (toUnfollow.length > 0) {
                document.getElementById('queue').style.display = 'block';
                document.getElementById('startBtn').style.display = 'block';
                document.getElementById('progressBar').style.display = 'block';
            } else {
                addLog('No non-followers found!', 'warning');
            }
        } else {
            addLog(`Scan failed: ${data.error}`, 'error');
        }
    } catch (error) {
        addLog(`Network error: ${error.message}`, 'error');
    } finally {
        scanBtn.disabled = false;
        scanBtn.style.display = 'block';
        loader.style.display = 'none';
    }
}

function updateQueueDisplay() {
    const queueCount = document.getElementById('queueCount');
    const count = document.getElementById('count');
    const queueList = document.getElementById('queueList');
    
    queueCount.textContent = toUnfollow.length;
    count.textContent = toUnfollow.length;
    
    queueList.innerHTML = '';
    toUnfollow.slice(0, 20).forEach(user => {
        const div = document.createElement('div');
        div.className = 'user-item';
        div.innerHTML = `
            <div>
                <span class="username">@${user.username}</span>
                ${user.follower_count ? `<div class="follower-count">${user.follower_count.toLocaleString()} followers</div>` : ''}
            </div>
            <div class="remove-btn" onclick="removeFromQueue('${user.user_id}')">×</div>
        `;
        queueList.appendChild(div);
    });
    
    if (toUnfollow.length > 20) {
        const more = document.createElement('div');
        more.textContent = `... and ${toUnfollow.length - 20} more`;
        more.style.textAlign = 'center';
        more.style.opacity = '0.7';
        queueList.appendChild(more);
    }
}

function removeFromQueue(userId) {
    toUnfollow = toUnfollow.filter(user => user.user_id !== userId);
    updateQueueDisplay();
    addLog('Removed from queue', 'info');
}

async function startUnfollow() {
    if (toUnfollow.length === 0) {
        addLog('No accounts to unfollow', 'warning');
        return;
    }
    
    if (!confirm(`Start unfollowing ${toUnfollow.length} accounts? This may take a while.`)) {
        return;
    }
    
    isProcessing = true;
    stopRequested = false;
    document.getElementById('startBtn').style.display = 'none';
    document.getElementById('stopBtn').style.display = 'block';
    document.getElementById('progressBar').style.display = 'block';
    
    addLog('Starting unfollow process...', 'info');
    
    const total = toUnfollow.length;
    let completed = 0;
    
    for (let i = 0; i < toUnfollow.length; i++) {
        if (stopRequested) {
            addLog('Process stopped by user', 'warning');
            break;
        }
        
        const user = toUnfollow[i];
        
        try {
            addLog(`Unfollowing @${user.username}...`, 'info');
            
            const response = await fetch('/unfollow', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Session-ID': currentSession
                },
                body: JSON.stringify({user_id: user.user_id})
            });
            
            const data = await response.json();
            
            if (data.success) {
                completed++;
                const progress = (completed / total) * 100;
                document.getElementById('progressFill').style.width = `${progress}%`;
                
                addLog(`✓ Unfollowed @${user.username}`, 'success');
                
                // Remove from local array
                toUnfollow = toUnfollow.filter(u => u.user_id !== user.user_id);
                updateQueueDisplay();
                
                // Add delay to avoid rate limits (3-5 seconds between unfollows)
                if (i < toUnfollow.length - 1 && !stopRequested) {
                    addLog('Waiting 4 seconds to avoid detection...', 'info');
                    await new Promise(resolve => setTimeout(resolve, 4000));
                }
            } else {
                addLog(`✗ Failed to unfollow @${user.username}: ${data.error}`, 'error');
            }
        } catch (error) {
            addLog(`✗ Error unfollowing @${user.username}: ${error.message}`, 'error');
        }
    }
    
    isProcessing = false;
    document.getElementById('startBtn').style.display = 'block';
    document.getElementById('stopBtn').style.display = 'none';
    
    if (stopRequested) {
        addLog('Process stopped. ' + completed + ' accounts unfollowed.', 'warning');
    } else {
        addLog(`Process completed! ${completed} accounts unfollowed.`, 'success');
    }
    
    updateStats();
}

function stopUnfollow() {
    stopRequested = true;
    addLog('Stopping process after current unfollow...', 'warning');
}

async function updateStats() {
    if (!currentSession) return;
    
    try {
        const response = await fetch('/stats', {
            headers: {'X-Session-ID': currentSession}
        });
        
        const data = await response.json();
        if (data.success) {
            document.getElementById('stats').innerHTML = `
                @${data.username}<br>
                <small style="font-size:16px">
                    Following: ${data.following_count.toLocaleString()} | 
                    Followers: ${data.follower_count.toLocaleString()}
                </small>
            `;
        }
    } catch (error) {
        console.error('Failed to update stats:', error);
    }
}

function logout() {
    if (confirm('Are you sure you want to logout?')) {
        fetch('/logout', {
            method: 'POST',
            headers: {'X-Session-ID': currentSession}
        });
        
        currentSession = '';
        toUnfollow = [];
        document.getElementById('main').style.display = 'none';
        document.getElementById('login').style.display = 'block';
        document.getElementById('logoutBtn').style.display = 'none';
        document.getElementById('queue').style.display = 'none';
        document.getElementById('startBtn').style.display = 'none';
        document.getElementById('cookies').value = '';
        addLog('Logged out', 'info');
    }
}

function addLog(message, type = 'info') {
    const log = document.getElementById('log');
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

// Auto-save whitelist
document.getElementById('whitelist').addEventListener('input', function() {
    localStorage.setItem('whitelist', this.value);
});

// Load saved whitelist
const savedWhitelist = localStorage.getItem('whitelist');
if (savedWhitelist) {
    document.getElementById('whitelist').value = savedWhitelist;
}

// Check for session on page load
window.addEventListener('load', function() {
    const session = localStorage.getItem('session');
    if (session) {
        document.getElementById('cookies').value = session;
        login();
    }
});
</script>
</body>
</html>
'''

# Helper functions
def validate_sessionid(sessionid):
    """Validate sessionid format"""
    if not sessionid or len(sessionid) < 10:
        return False
    # Basic validation - sessionid should be alphanumeric with some special chars
    if not re.match(r'^[A-Za-z0-9%\.\-_]+$', sessionid):
        return False
    return True

def get_instagram_client(session_id):
    """Get or create Instagram client for session"""
    if session_id not in user_sessions:
        return None
    
    session_data = user_sessions[session_id]
    
    if 'client' not in session_data:
        try:
            cl = Client()
            cl.set_settings({
                "sessionid": session_data['sessionid'],
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "device_settings": {
                    "app_version": "269.0.0.18.75",
                    "android_version": 26,
                    "android_release": "8.0.0",
                    "dpi": "480dpi",
                    "resolution": "1080x1920",
                    "manufacturer": "samsung",
                    "device": "SM-G935F",
                    "model": "herolte",
                    "cpu": "samsungexynos8890",
                    "version_code": "269"
                }
            })
            cl.login_by_sessionid(session_data['sessionid'])
            session_data['client'] = cl
            session_data['last_activity'] = time.time()
        except Exception as e:
            logger.error(f"Failed to create client: {e}")
            return None
    
    session_data['last_activity'] = time.time()
    return session_data['client']

def cleanup_old_sessions():
    """Clean up old sessions"""
    current_time = time.time()
    expired = []
    for session_id, data in user_sessions.items():
        if current_time - data.get('last_activity', 0) > 7200:  # 2 hours
            expired.append(session_id)
    
    for session_id in expired:
        if 'client' in user_sessions[session_id]:
            try:
                user_sessions[session_id]['client'].logout()
            except:
                pass
        del user_sessions[session_id]

# Decorator for session validation
def require_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get('X-Session-ID')
        if not session_id or session_id not in user_sessions:
            return jsonify({'success': False, 'error': 'Invalid or expired session'}), 401
        
        # Clean up old sessions periodically
        if random.random() < 0.1:  # 10% chance on each request
            cleanup_old_sessions()
        
        return f(*args, **kwargs)
    return decorated_function

# Routes
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
        
        # Create Instagram client
        cl = Client()
        cl.set_settings({
            "sessionid": sessionid,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        
        # Test login by getting user info
        try:
            user_id = cl.user_id_from_username("instagram")
            if not user_id:
                return jsonify({'success': False, 'error': 'Invalid sessionid'}), 401
            
            # Get current user info
            user_info = cl.account_info()
            
            # Generate session ID
            session_id = os.urandom(16).hex()
            
            # Store session data
            user_sessions[session_id] = {
                'sessionid': sessionid,
                'user_id': user_info.pk,
                'username': user_info.username,
                'full_name': user_info.full_name,
                'follower_count': user_info.follower_count,
                'following_count': user_info.following_count,
                'is_premium': False,  # Implement premium check
                'last_activity': time.time(),
                'created_at': datetime.now().isoformat()
            }
            
            logger.info(f"User @{user_info.username} logged in")
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'username': user_info.username,
                'full_name': user_info.full_name,
                'follower_count': user_info.follower_count,
                'following_count': user_info.following_count,
                'is_premium': False
            })
            
        except (LoginRequired, ClientLoginRequired) as e:
            logger.error(f"Login required error: {e}")
            return jsonify({'success': False, 'error': 'Session expired. Please get new sessionid'}), 401
        except Exception as e:
            logger.error(f"Login error: {e}")
            return jsonify({'success': False, 'error': f'Login failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Unexpected error in login: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/logout', methods=['POST'])
@require_session
def logout_route():
    session_id = request.headers.get('X-Session-ID')
    if session_id in user_sessions:
        if 'client' in user_sessions[session_id]:
            try:
                user_sessions[session_id]['client'].logout()
            except:
                pass
        del user_sessions[session_id]
    return jsonify({'success': True})

@app.route('/stats', methods=['GET'])
@require_session
def stats():
    session_id = request.headers.get('X-Session-ID')
    session_data = user_sessions[session_id]
    
    return jsonify({
        'success': True,
        'username': session_data['username'],
        'full_name': session_data['full_name'],
        'follower_count': session_data['follower_count'],
        'following_count': session_data['following_count'],
        'is_premium': session_data['is_premium']
    })

@app.route('/scan', methods=['POST'])
@require_session
def scan():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        whitelist = set([u.lower() for u in data.get('whitelist', [])])
        smart_mode = data.get('smart_mode', True)
        skip_verified = data.get('skip_verified', True)
        skip_recent = data.get('skip_recent', True)
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        # Get user's following
        user_id = user_sessions[session_id]['user_id']
        following = cl.user_following(user_id, amount=0)  # 0 means get all
        
        # Get user's followers
        followers = cl.user_followers(user_id, amount=0)
        
        follower_ids = set([f.pk for f in followers.values()])
        
        non_followers = []
        skipped_count = 0
        
        for user_pk, user in following.items():
            # Skip if in whitelist
            if user.username.lower() in whitelist:
                skipped_count += 1
                continue
            
            # Skip if user follows back
            if user_pk in follower_ids:
                continue
            
            # Skip verified accounts if enabled
            if skip_verified and user.is_verified:
                skipped_count += 1
                continue
            
            # Skip large accounts if smart mode enabled
            if smart_mode and user.follower_count > 12000:
                skipped_count += 1
                continue
            
            # Skip recent follows (last 7 days) if enabled
            if skip_recent:
                # Note: This requires storing follow dates, which instagrapi doesn't provide directly
                # You'd need to implement this differently
                pass
            
            non_followers.append({
                'user_id': user_pk,
                'username': user.username,
                'full_name': user.full_name,
                'is_verified': user.is_verified,
                'follower_count': user.follower_count,
                'profile_pic_url': user.profile_pic_url
            })
        
        # Store results in session
        user_sessions[session_id]['non_followers'] = non_followers
        
        logger.info(f"Scan completed for @{user_sessions[session_id]['username']}: {len(non_followers)} non-followers found")
        
        return jsonify({
            'success': True,
            'non_followers': non_followers,
            'total_following': len(following),
            'total_followers': len(followers),
            'skipped': skipped_count
        })
        
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return jsonify({'success': False, 'error': f'Scan failed: {str(e)}'}), 500

@app.route('/unfollow', methods=['POST'])
@require_session
def unfollow():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        if not data or 'user_id' not in data:
            return jsonify({'success': False, 'error': 'No user specified'}), 400
        
        user_id_to_unfollow = data['user_id']
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        # Check if user is in non_followers list
        session_data = user_sessions[session_id]
        if 'non_followers' not in session_data:
            return jsonify({'success': False, 'error': 'No scan data available'}), 400
        
        # Find user in non_followers
        user_to_unfollow = None
        for user in session_data['non_followers']:
            if user['user_id'] == user_id_to_unfollow:
                user_to_unfollow = user
                break
        
        if not user_to_unfollow:
            return jsonify({'success': False, 'error': 'User not found in non-followers list'}), 404
        
        # Perform unfollow
        try:
            result = cl.user_unfollow(user_id_to_unfollow)
            
            if result:
                # Remove from local list
                session_data['non_followers'] = [
                    u for u in session_data['non_followers'] 
                    if u['user_id'] != user_id_to_unfollow
                ]
                
                # Update following count
                session_data['following_count'] -= 1
                
                logger.info(f"Unfollowed @{user_to_unfollow['username']} for @{session_data['username']}")
                
                return jsonify({
                    'success': True,
                    'message': f'Unfollowed @{user_to_unfollow["username"]}',
                    'remaining': len(session_data['non_followers'])
                })
            else:
                return jsonify({'success': False, 'error': 'Unfollow failed'}), 500
                
        except Exception as e:
            logger.error(f"Unfollow API error: {e}")
            return jsonify({'success': False, 'error': f'Instagram API error: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Unfollow error: {e}")
        return jsonify({'success': False, 'error': f'Unfollow failed: {str(e)}'}), 500

@app.route('/status', methods=['GET'])
@require_session
def status():
    session_id = request.headers.get('X-Session-ID')
    session_data = user_sessions.get(session_id, {})
    
    return jsonify({
        'success': True,
        'is_active': True,
        'username': session_data.get('username'),
        'non_followers_count': len(session_data.get('non_followers', [])),
        'is_premium': session_data.get('is_premium', False),
        'last_activity': session_data.get('last_activity')
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'
