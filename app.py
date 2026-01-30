from flask import Flask, render_template_string, request, jsonify
from flask_wtf.csrf import CSRFProtect
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import os
import json
import time
import logging
import re
import threading
from functools import wraps
from datetime import datetime, timedelta

# 🛡️ Security Setup
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['WTF_CSRF_TIME_LIMIT'] = 3600

# Configure CSRF to look for the header sent by your JS
csrf = CSRFProtect(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory storage
user_sessions = {}

# ---------------------------------------------------------
# 1. FIXED: Helper to Manage Device Settings (Prevents Bans)
# ---------------------------------------------------------
def get_instagram_client(session_id):
    if session_id not in user_sessions:
        return None
    
    session_data = user_sessions[session_id]
    
    try:
        cl = Client()
        
        # ✅ Load saved device settings if they exist to prevent "New Device" flags
        if 'device_settings' in session_data:
            cl.set_settings(session_data['device_settings'])
        
        cl.login_by_sessionid(session_data['sessionid'])
        return cl
    except Exception as e:
        logger.error(f"Failed to create client: {e}")
        return None

def validate_sessionid(sessionid):
    # Basic validation
    if not sessionid or len(sessionid) < 10:
        return False
    return True

# Decorator for session validation
def require_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_id = request.headers.get('X-Session-ID')
        if not session_id or session_id not in user_sessions:
            return jsonify({'success': False, 'error': 'Invalid or expired session'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route('/')
def index():
    # (Assuming HTML variable is defined elsewhere as in your snippet)
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
            
            # ✅ Save device settings immediately
            device_settings = cl.get_settings()
            
            session_id = os.urandom(16).hex()
            
            user_sessions[session_id] = {
                'sessionid': sessionid,
                'device_settings': device_settings, # ✅ Store settings
                'user_id': user_info.pk,
                'username': user_info.username,
                'following_count': user_info.following_count,
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
            return jsonify({'success': False, 'error': f'Login failed: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/scan', methods=['POST'])
@require_session
def scan():
    try:
        session_id = request.headers.get('X-Session-ID')
        data = request.get_json()
        
        # Options
        whitelist = set([u.lower().strip() for u in data.get('whitelist', [])])
        smart_mode = data.get('smart_mode', True)
        
        cl = get_instagram_client(session_id)
        if not cl:
            return jsonify({'success': False, 'error': 'Session expired'}), 401
        
        session_data = user_sessions[session_id]
        user_id = session_data['user_id']
        
        # ✅ Optimization: Use SET for O(1) lookups
        # Note: amount=None is dangerous for big accounts. 
        # Ideally, limit this or use pagination.
        try:
            followers_map = {str(f.pk) for f in cl.user_followers_v1(user_id, amount=2000)}
            following_list = cl.user_following_v1(user_id, amount=2000)
        except Exception as e:
             return jsonify({'success': False, 'error': f'InstaAPI Error: {str(e)}'}), 500
        
        non_followers = []
        
        for user in following_list:
            # Check whitelist
            if user.username.lower() in whitelist:
                continue
            
            # ✅ Check if they follow back (Instant Lookup)
            if str(user.pk) in followers_map:
                continue
            
            # Smart Mode Checks
            if smart_mode:
                if user.is_verified: continue
                if user.follower_count > 10000: continue # Likely a creator/brand
                
            non_followers.append({
                'user_id': str(user.pk), # ✅ Send as String to JS to avoid Int overflow
                'username': user.username,
                'follower_count': user.follower_count,
                'is_verified': user.is_verified
            })
        
        # Update session
        session_data['non_followers'] = non_followers
        
        return jsonify({
            'success': True,
            'non_followers': non_followers[:100], # Send chunk to UI
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
            
            # ✅ 1. REMOVE user from local session list
            session_data = user_sessions[session_id]
            session_data['non_followers'] = [
                u for u in session_data['non_followers'] 
                if str(u['user_id']) != str(user_id_to_unfollow)
            ]
            
            return jsonify({'success': True})
            
        except ClientError as e:
            if e.status_code == 429:
                # ✅ 2. Handle Rate Limit Gracefully
                return jsonify({'success': False, 'error': 'Rate limit hit. Waiting...'}), 429
            logger.error(f"Insta Error: {e}")
            return jsonify({'success': False, 'error': 'Instagram API Error'}), 500
        
    except Exception as e:
        # ✅ 3. FIXED: Removed 'addLog' call
        logger.error(f"Unfollow error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Include your HTML variable here...
HTML = '''...'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
