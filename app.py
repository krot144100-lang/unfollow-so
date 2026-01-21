from flask import Flask, render_template_string, request, jsonify, session
from instagrapi import Client
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret-key-2025")

HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unfollow Ninja – safest non-followers remover 2025</title>
    <style>
        :root{--bg:#fff;--text:#000;--accent:#e91e63;--red:#d32f2f}
        .dark{--bg:#0f0f0f;--text:#fff;--accent:#ff4081}
        body{font-family:system-ui;background:var(--bg);color:var(--text);margin:0;padding:20px 15px}
        .container{max-width:520px;margin:auto}
        .toggle{position:fixed;top:15px;right:15px;background:var(--accent);color:white;width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;cursor:pointer;z-index:999}
        h1{font-size:28px;text-align:center;margin:10px 0 5px}
        textarea{width:100%;padding:14px;margin:10px 0;border-radius:12px;border:1px solid #333;background:var(--bg);color:var(--text);box-sizing:border-box;font-size:15px;height:130px}
        button{background:var(--accent);color:white;border:none;padding:16px;border-radius:12px;font-size:18px;width:100%;margin:15px 0;cursor:pointer;font-weight:bold}
        .log{background:#000;color:#0f0;padding:15px;border-radius:12px;height:320px;overflow-y:auto;font-family:monospace;margin:20px 0;font-size:14px;line-height:1.6}
        .queue{background:rgba(0,0,0,0.1);padding:15px;border-radius:12px;margin:20px 0;max-height:300px;overflow-y:auto}
        .user{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.1)}
        .pay{background:var(--accent);color:white;padding:16px 28px;border-radius:12px;text-decoration:none;display:inline-block;margin:20px auto;font-weight:bold;font-size:18px}
    </style>
</head>
<body>
<div class="container">
    <div class="toggle" onclick="document.body.classList.toggle('dark')">☀︎</div>
    <h1>Unfollow Ninja</h1>
    <p>The safest Instagram non-followers cleaner 2025</p>

    <div id="login">
        <textarea id="cookies" placeholder="Paste sessionid only (easiest way) or full cookies"></textarea>
        <button onclick="login()">Login with Cookies →</button>
        <small style="opacity:0.7;display:block;margin-top:8px">Instagram web → F12 → Application → Cookies → copy value of sessionid</small>
    </div>

    <div id="main" style="display:none">
        <div style="font-size:26px;font-weight:bold;text-align:center;margin:25px 0;color:var(--accent)" id="stats">Loading...</div>
        <textarea id="whitelist" placeholder="Whitelist – one username per line"></textarea>
        <label style="display:block;margin:15px 0"><input type="checkbox" id="smart" checked> Smart mode (protect big accounts + recent likers)</label>
        <button onclick="scan()">Scan Non-Followers</button>
        <div id="queue" class="queue" style="display:none"></div>
        <button id="startBtn" onclick="startUnfollow()" style="display:none;background:var(--red)">Start Cleaning (<span id="count">0</span>)</button>
        <div class="log" id="log">Ready.</div>
        <div style="text-align:center;margin:50px 0">
            <p>Made by <a href="https://x.com/krot13" target="_blank" style="color:var(--accent);text-decoration:none">@krot13</a></p>
            <a href="https://ko-fi.com/krot13" target="_blank" class="pay">⚡ Lifetime Unlimited – $9 one-time</a>
        </div>
    </div>
</div>

<script>
let toUnfollow = [];
async function login(){
    const c = document.getElementById('cookies').value.trim();
    if(!c) return alert("Paste sessionid or cookies");
    const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cookies:c})});
    const d = await r.json();
    if(d.success){
        document.getElementById('login').style.display='none';
        document.getElementById('main').style.display='block';
        document.getElementById('stats').innerText = `Logged as @${d.username} ✅`;
    } else alert(d.error || "Login failed")
}
async function scan(){
    const w = document.getElementById('whitelist').value.split('\\n').map(x=>x.trim().toLowerCase()).filter(x=>x);
    const s = document.getElementById('smart').checked;
    const res = await fetch('/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({whitelist:w,smart:s})});
    const data = await res.json();
    toUnfollow = data.users || [];
    document.getElementById('count').innerText = toUnfollow.length;
    document.getElementById('startBtn').style.display = 'block';
    let h = '<h3 style="margin:0 0 15px">Will unfollow '+toUnfollow.length+' users:</h3>';
    toUnfollow.forEach(u=>h+=`<div class="user">@${u.username}<span>${u.followers.toLocaleString()} followers</span></div>`);
    document.getElementById('queue').innerHTML = h || "<p>🎉 No non-followers found!</p>";
    document.getElementById('queue').style.display = 'block';
}
async function startUnfollow(){
    document.getElementById('startBtn').disabled = true;
    const log = document.getElementById('log');
    let i = 0;
    for(const u of toUnfollow){
        i++;
        if(i > 200){
            log.innerHTML += '<br>⚡ Daily limit 200 reached → <a href="https://ko-fi.com/krot13" target="_blank">Upgrade $9 lifetime</a>';
            break;
        }
        const res = await fetch('/unfollow', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:u.pk})});
        const r = await res.json();
        log.innerHTML += r.success ? `✅ ${i}. @${u.username}<br>` : `❌ ${i}. @${u.username}<br>`;
        log.scrollTop = log.scrollHeight;
        await new Promise(t => setTimeout(t, 9000 + Math.random()*9000));
    }
    log.innerHTML += '<br><strong>Done!</strong>';
}
</script>
</body>
</html>
'''

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/login", methods=["POST"])
def login():
    try:
        cookies = request.json["cookies"]
        cl = Client()
        cl.delay_range = [1, 6]
        if "sessionid" in cookies and "=" in cookies:
            sessionid = cookies.split("sessionid=")[1].split(";")[0]
        else:
            sessionid = json.loads(cookies).get("sessionid") if cookies.startswith("{") else cookies
        cl.login_by_sessionid(sessionid)
        session["settings"] = cl.dump_settings()
        return jsonify({"success": True, "username": cl.account_info().username})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/scan", methods=["POST"])
def scan():
    try:
        cl = Client()
        cl.load_settings(session["settings"])
        data = request.json
        whitelist = set(data["whitelist"])
        smart = data["smart"]
        following = cl.user_following(cl.user_id)
        followers = cl.user_followers(cl.user_id)
        non_followers = {k: v for k, v in following.items() if k not in followers}
        users = []
        for uid, user in list(non_followers.items())[:1200]:
            if user.username.lower() in whitelist: continue
            if smart and user.follower_count > 12000: continue
            users.append({"pk": user.pk, "username": user.username, "followers": user.follower_count})
        return jsonify({"users": users})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/unfollow", methods=["POST"])
def unfollow():
    try:
        cl = Client()
        cl.load_settings(session["settings"])
        cl.user_unfollow(request.json["user_id"])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
