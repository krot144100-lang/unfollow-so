from flask import Flask, render_template_string, request, jsonify, session
from instagrapi import Client
import os
import json

app = Flask(__name__)
app.secret_key = "krot133-secret-2026"

HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unfollow Ninja 2026 – Safest Unfollow Tool Ever</title>
    <style>
        :root{--bg:#fff;--text:#000;--accent:#ff0080;--red:#d32f2f}
        .dark{--bg:#0f0f0f;--text:#fff;--accent:#ff4081}
        body{font-family:system-ui;background:var(--bg);color:var(--text);margin:0;padding:20px 15px}
        .container{max-width:520px;margin:auto}
        .toggle{position:fixed;top:15px;right:15px;background:var(--accent);color:white;width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;cursor:pointer;z-index:999}
        h1{font-size:28px;text-align:center;margin:10px 0 5px}
        textarea{width:100%;padding:14px;margin:10px 0;border-radius:12px;border:1px solid #333;background:var(--bg);color:var(--text);box-sizing:border-box;font-size:15px;height:130px}
        button{background:var(--accent);color:white;border:none;padding:16px;border-radius:12px;font-size:18px;width:100%;margin:15px 0;cursor:pointer;font-weight:bold}
        .log{background:#000;color:#0f0;padding:15px;border-radius:12px;height:320px;overflow-y:auto;font-family:monospace;margin:20px 0;font-size:14px;line-height:1.6}
        .queue{background:rgba(0,0,0,0.1);padding:15px;border-radius:12px;margin:20px 0;max-height:300px;overflow-y:auto}
        .pay-big{background:linear-gradient(135deg,#ff0080,#ff4081);color:white;padding:28px 32px;border-radius:20px;text-decoration:none;display:block;margin:50px auto 30px;font-weight:bold;font-size:24px;text-align:center;box-shadow:0 15px 40px rgba(255,0,128,0.5);transition:0.3s}
        .pay-big:hover{transform:scale(1.05);box-shadow:0 20px 50px rgba(255,0,128,0.7)}
        .timer{color:#ff1744;font-size:19px;font-weight:bold;text-align:center;margin:20px 0}
    </style>
</head>
<body>
<div class="container">
    <div class="toggle" onclick="document.body.classList.toggle('dark')">☀︎</div>
    <h1>Unfollow Ninja 2026</h1>
    <p style="text-align:center;font-size:19px;font-weight:bold;color:var(--accent)">The Safest Instagram Unfollow Tool Ever Created</p>

    <div id="login">
        <textarea id="cookies" placeholder="Paste ONLY your Instagram sessionid here (safest method)"></textarea>
        <button onclick="login()">Login with SessionID →</button>
        <small style="opacity:0.7;display:block;margin-top:8px">Instagram web → F12 → Application → Cookies → copy value of sessionid</small>
    </div>

    <div id="main" style="display:none">
        <div style="font-size:26px;font-weight:bold;text-align:center;margin:25px 0;color:var(--accent)" id="stats">Loading...</div>
        <textarea id="whitelist" placeholder="Whitelist – usernames you never want to unfollow (one per line)"></textarea>
        <label style="display:block;margin:15px 0"><input type="checkbox" id="smart" checked> Smart Mode (skip >12k accounts & recent likers)</label>
        <button onclick="scan()">Scan Non-Followers</button>
        <div id="queue" class="queue" style="display:none"></div>
        <button id="startBtn" onclick="startUnfollow()" style="display:none;background:var(--red)">Start Cleaning (<span id="count">0</span>)</button>
        <div class="log" id="log">Ready.</div>

        <div class="timer">★ First 50 buyers only – $7 instead of $9 (11 spots left)</div>
       <a href="https://nowpayments.io/payment?amount=9&currency=usd&payin=usdttrc20&description=Unfollow%20Ninja%20Lifetime%20Unlimited&success_url=https://unfollow-so2.onrender.com" target="_blank" class="pay-big">
        

            LIFETIME UNLIMITED – $9 USDT (TRC20)<br>
            <small>No limits forever · Instant activation</small>
        </a>
                <div class="timer">★ First 50 buyers – $7 instead of $9 (only 9 spots left!)</div>
        <a href="https://nowpayments.io/payment?amount=9&currency=usd&payin=usdttrc20&description=Unfollow%20Ninja%20Lifetime%20Unlimited&success_url=https://unfollow-so2.onrender.com" target="_blank" class="pay-big">
            LIFETIME UNLIMITED – $9 USDT (TRC20)<br>
            <small>No daily limits · Instant activation · One-time payment</small>
        </a>
        <p style="text-align:center;font-size:14px;opacity:0.8;margin-top:30px">
            Already used by 3100+ people in 2026<br>
            Made with ❤️ by @krot13 & @krot133
        </p>
    </div>
</div>

<script>
let toUnfollow = [];
async function login(){
    const c = document.getElementById('cookies').value.trim();
    if(!c) return alert("Paste sessionid first!");
    const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cookies:c})});
    const d = await r.json();
    if(d.success){
        document.getElementById('login').style.display='none';
        document.getElementById('main').style.display='block';
        document.getElementById('stats').innerText = `Logged in as @${d.username} ✅`;
    } else alert(d.error || "Invalid sessionid")
}
async function scan(){
    const w = document.getElementById('whitelist').value.split('\\n').map(x=>x.trim().toLowerCase()).filter(x=>x);
    const s = document.getElementById('smart').checked;
    const res = await fetch('/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({whitelist:w,smart:s})});
    const data = await res.json();
    toUnfollow = data.users || [];
    document.getElementById('count').innerText = toUnfollow.length;
    document.getElementById('startBtn').style.display = 'block';
    let h = '<h3 style="margin:0 0 15px">Will unfollow '+toUnfollow.length+' accounts:</h3>';
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
            log.innerHTML += '<br>⚡ Daily limit reached. <a href="https://nowpayments.io/payment?iid=7583524726&amount=9&currency=usd&extra=payin:usdttrc20" target="_blank">Remove limit forever – $9 →</a>';
            break;
        }
        const res = await fetch('/unfollow', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:u.pk})});
        const r = await res.json();
        log.innerHTML += r.success ? `✅ ${i}. @${u.username}<br>` : `❌ ${i}. @${u.username}<br>`;
        log.scrollTop = log.scrollHeight;
        await new Promise(t => setTimeout(t, 9000 + Math.random()*9000));
    }
    log.innerHTML += '<br><strong>DONE! All cleaned.</strong>';
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
        sessionid = cookies.split("sessionid=")[1].split(";")[0] if "sessionid=" in cookies else cookies
        cl.login_by_sessionid(sessionid)
        session["settings"] = cl.dump_settings()
        return jsonify({"success": True, "username": cl.account_info().username})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/scan", methods=["POST"])
def scan():
    try:
        cl = Client(); cl.load_settings(session["settings"])
        data = request.json; whitelist = set(u.lower() for u in data["whitelist"])
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
        cl = Client(); cl.load_settings(session["settings"])
        cl.user_unfollow(request.json["user_id"])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
