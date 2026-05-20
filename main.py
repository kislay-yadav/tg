import asyncio, os, random, json, logging, sys
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

# ═══════════════════════════════════════════
#             CONFIGURATION
# ═══════════════════════════════════════════
API_ID       = 21952127
API_HASH     = "e0a3741bb3b132947d86d8fc6218eebe"
BOT_TOKEN    = "8821493954:AAFtjGU4PWqyJl-tTASuI3HRBMUl6ZQ18AE"
OWNER_ID     = int(os.environ.get("OWNER_ID", "8156053366"))
RAILWAY_URL  = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "tg-production-4302.up.railway.app")
PORT         = int(os.environ.get("PORT", "8080"))
SESSION_FILE = "/tmp/tg_session.json"
MIN_DELAY    = 45
MAX_DELAY    = 120
CYCLE_MIN    = 3600
CYCLE_MAX    = 7200

logging.basicConfig(level=logging.WARNING)
for lib in ("telegram", "httpx", "telethon", "aiohttp"):
    logging.getLogger(lib).setLevel(logging.ERROR)

console = Console()

# ═══════════════════════════════════════════
#               STATE
# ═══════════════════════════════════════════
state = {
    "session"   : None,
    "phone"     : None,
    "code_hash" : None,
    "client"    : None,
    "logged_in" : False,
    "awaiting"  : None,
    "groups"    : [],
    "message"   : "",
    "running"   : False,
    "task"      : None,
    "stats"     : {"sent": 0, "failed": 0, "cycles": 0},
    "otp_pending": False,
    "needs_2fa" : False,
}

# ═══════════════════════════════════════════
#           SESSION HELPERS
# ═══════════════════════════════════════════
def save_session(s, phone):
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump({"session": s, "phone": phone}, f)
    except Exception:
        pass

def load_session():
    env_s = os.environ.get("SESSION_STRING", "")
    if env_s:
        return env_s, None
    try:
        with open(SESSION_FILE) as f:
            d = json.load(f)
            return d.get("session"), d.get("phone")
    except Exception:
        return None, None

async def notify(bot, text):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="Markdown")
    except Exception:
        pass

# ═══════════════════════════════════════════
#         WEB LOGIN PAGE (HTML)
# ═══════════════════════════════════════════
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Login</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);
    font-family:'Segoe UI',sans-serif;color:#fff;
  }
  .card{
    background:rgba(255,255,255,0.06);backdrop-filter:blur(20px);
    border:1px solid rgba(255,255,255,0.12);border-radius:24px;
    padding:48px 40px;width:100%;max-width:420px;
    box-shadow:0 32px 64px rgba(0,0,0,0.4);
  }
  .logo{text-align:center;margin-bottom:32px}
  .logo svg{width:64px;height:64px}
  h1{text-align:center;font-size:24px;font-weight:700;margin-bottom:6px;
     background:linear-gradient(90deg,#54a0ff,#a29bfe);-webkit-background-clip:text;
     -webkit-text-fill-color:transparent}
  .sub{text-align:center;color:rgba(255,255,255,0.45);font-size:13px;margin-bottom:36px}
  .step{display:none}
  .step.active{display:block}
  label{display:block;font-size:12px;font-weight:600;letter-spacing:.8px;
        text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:8px}
  input{
    width:100%;padding:14px 18px;border-radius:12px;border:1.5px solid rgba(255,255,255,0.12);
    background:rgba(255,255,255,0.07);color:#fff;font-size:16px;outline:none;
    transition:border-color .2s,box-shadow .2s;letter-spacing:1px;
  }
  input:focus{border-color:#54a0ff;box-shadow:0 0 0 3px rgba(84,160,255,0.18)}
  input::placeholder{color:rgba(255,255,255,0.25);letter-spacing:0}
  .btn{
    width:100%;padding:15px;border-radius:12px;border:none;cursor:pointer;
    font-size:15px;font-weight:700;letter-spacing:.5px;margin-top:20px;
    background:linear-gradient(135deg,#54a0ff,#a29bfe);color:#fff;
    transition:opacity .2s,transform .1s;
  }
  .btn:hover{opacity:.9}
  .btn:active{transform:scale(.98)}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .msg{margin-top:16px;padding:12px 16px;border-radius:10px;font-size:13px;
       text-align:center;display:none}
  .msg.error{background:rgba(255,71,87,0.15);border:1px solid rgba(255,71,87,0.3);color:#ff6b81}
  .msg.success{background:rgba(46,213,115,0.12);border:1px solid rgba(46,213,115,0.25);color:#2ed573}
  .msg.info{background:rgba(84,160,255,0.12);border:1px solid rgba(84,160,255,0.25);color:#74b9ff}
  .otp-hint{font-size:12px;color:rgba(255,255,255,0.35);text-align:center;
             margin-top:12px;line-height:1.6}
  .timer{font-size:13px;color:#feca57;text-align:center;margin-top:10px;font-weight:600}
  .resend{color:#54a0ff;cursor:pointer;font-size:13px;text-align:center;
          margin-top:10px;text-decoration:underline;display:none}
  .fa-check{color:#2ed573}
  .spinner{display:inline-block;width:18px;height:18px;border:2px solid rgba(255,255,255,0.3);
           border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;
           vertical-align:middle;margin-right:8px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .step-indicator{display:flex;gap:8px;justify-content:center;margin-bottom:28px}
  .dot{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,0.2);transition:.3s}
  .dot.active{background:#54a0ff;transform:scale(1.3)}
  .dot.done{background:#2ed573}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <svg viewBox="0 0 48 48" fill="none">
      <circle cx="24" cy="24" r="24" fill="url(#g)"/>
      <path d="M10 24l8 8 20-20" stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
      <defs><linearGradient id="g" x1="0" y1="0" x2="48" y2="48">
        <stop stop-color="#54a0ff"/><stop offset="1" stop-color="#a29bfe"/>
      </linearGradient></defs>
    </svg>
  </div>
  <h1>Telegram Auth</h1>
  <p class="sub">Secure login portal</p>

  <div class="step-indicator">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
  </div>

  <!-- STEP 1: PHONE -->
  <div class="step active" id="step1">
    <label>Phone Number</label>
    <input type="tel" id="phone" placeholder="+91 98765 43210" autocomplete="tel">
    <button class="btn" onclick="sendOTP()">Send OTP →</button>
    <div class="msg" id="msg1"></div>
  </div>

  <!-- STEP 2: OTP -->
  <div class="step" id="step2">
    <label>Enter OTP</label>
    <input type="text" id="otp" placeholder="1 2 3 4 5" maxlength="10"
           autocomplete="one-time-code" inputmode="numeric">
    <div class="otp-hint">Check your Telegram app for the code</div>
    <div class="timer" id="timer"></div>
    <button class="btn" onclick="verifyOTP()">Verify OTP →</button>
    <div class="resend" id="resendBtn" onclick="resendOTP()">🔁 Resend OTP</div>
    <div class="msg" id="msg2"></div>
  </div>

  <!-- STEP 3: 2FA -->
  <div class="step" id="step3">
    <label>2FA Password</label>
    <input type="password" id="twofa" placeholder="Your cloud password" autocomplete="current-password">
    <button class="btn" onclick="verify2FA()">Confirm →</button>
    <div class="msg" id="msg3"></div>
  </div>

  <!-- STEP 4: SUCCESS -->
  <div class="step" id="step4">
    <div style="text-align:center;padding:20px 0">
      <div style="font-size:56px;margin-bottom:16px">✅</div>
      <div style="font-size:20px;font-weight:700;color:#2ed573">Login Successful!</div>
      <div style="color:rgba(255,255,255,0.45);margin-top:8px;font-size:14px">
        Go back to your Telegram bot
      </div>
    </div>
  </div>
</div>

<script>
let timerInterval;

function showMsg(id, text, type){
  const el = document.getElementById(id);
  el.className = 'msg ' + type;
  el.textContent = text;
  el.style.display = 'block';
}

function setBtn(stepNum, loading){
  const btn = document.querySelector('#step'+stepNum+' .btn');
  if(loading){
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Please wait…';
  } else {
    btn.disabled = false;
    btn.innerHTML = stepNum==1?'Send OTP →': stepNum==2?'Verify OTP →':'Confirm →';
  }
}

function startTimer(seconds){
  let s = seconds;
  const el = document.getElementById('timer');
  const resend = document.getElementById('resendBtn');
  resend.style.display = 'none';
  clearInterval(timerInterval);
  timerInterval = setInterval(()=>{
    if(s <= 0){
      clearInterval(timerInterval);
      el.textContent = '⏰ Code expired';
      el.style.color = '#ff6b81';
      resend.style.display = 'block';
    } else {
      el.textContent = '⏳ Expires in ' + s + 's';
      s--;
    }
  }, 1000);
}

function goStep(n){
  document.querySelectorAll('.step').forEach((el,i)=>{
    el.classList.toggle('active', i+1===n);
  });
  document.querySelectorAll('.dot').forEach((d,i)=>{
    d.className = 'dot' + (i+1<n?' done': i+1===n?' active':'');
  });
}

async function sendOTP(){
  const phone = document.getElementById('phone').value.trim();
  if(!phone){ showMsg('msg1','Enter your phone number','error'); return; }
  setBtn(1, true);
  try{
    const r = await fetch('/send_otp', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({phone})
    });
    const d = await r.json();
    if(d.ok){
      goStep(2);
      startTimer(120);
    } else {
      showMsg('msg1', d.error || 'Failed to send OTP', 'error');
      setBtn(1, false);
    }
  } catch(e){
    showMsg('msg1','Network error. Try again.','error');
    setBtn(1, false);
  }
}

async function verifyOTP(){
  const otp = document.getElementById('otp').value.replace(/\\s/g,'');
  if(!otp){ showMsg('msg2','Enter the OTP','error'); return; }
  setBtn(2, true);
  try{
    const r = await fetch('/verify_otp', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({otp})
    });
    const d = await r.json();
    if(d.ok){
      clearInterval(timerInterval);
      goStep(4);
    } else if(d.needs_2fa){
      clearInterval(timerInterval);
      goStep(3);
    } else if(d.expired){
      showMsg('msg2','OTP expired! Click Resend.','error');
      setBtn(2,false);
    } else {
      showMsg('msg2', d.error || 'Invalid OTP','error');
      setBtn(2,false);
    }
  } catch(e){
    showMsg('msg2','Network error.','error');
    setBtn(2,false);
  }
}

async function resendOTP(){
  document.getElementById('resendBtn').style.display='none';
  document.getElementById('timer').textContent = '⏳ Resending…';
  try{
    const r = await fetch('/resend_otp', {method:'POST'});
    const d = await r.json();
    if(d.ok){
      startTimer(120);
      showMsg('msg2','New OTP sent to Telegram!','success');
    } else {
      showMsg('msg2', d.error || 'Resend failed','error');
    }
  } catch(e){
    showMsg('msg2','Network error.','error');
  }
}

async function verify2FA(){
  const pwd = document.getElementById('twofa').value;
  if(!pwd){ showMsg('msg3','Enter your password','error'); return; }
  setBtn(3,true);
  try{
    const r = await fetch('/verify_2fa', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({password: pwd})
    });
    const d = await r.json();
    if(d.ok){ goStep(4); }
    else {
      showMsg('msg3', d.error || 'Wrong password','error');
      setBtn(3,false);
    }
  } catch(e){
    showMsg('msg3','Network error.','error');
    setBtn(3,false);
  }
}

// Enter key support
document.addEventListener('keydown', e=>{
  if(e.key!=='Enter') return;
  const step = document.querySelector('.step.active');
  if(!step) return;
  const id = step.id;
  if(id==='step1') sendOTP();
  else if(id==='step2') verifyOTP();
  else if(id==='step3') verify2FA();
});
</script>
</body>
</html>"""

# ═══════════════════════════════════════════
#         WEB SERVER ROUTES
# ═══════════════════════════════════════════
routes = web.RouteTableDef()

@routes.get("/")
async def index(req):
    return web.Response(text=LOGIN_HTML, content_type="text/html")

@routes.get("/health")
async def health(req):
    return web.Response(text="OK")

@routes.post("/send_otp")
async def send_otp(req):
    try:
        data  = await req.json()
        phone = data.get("phone", "").strip()
        if not phone:
            return web.json_response({"ok": False, "error": "Phone required"})

        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        res = await c.send_code_request(phone)

        state["phone"]      = phone
        state["code_hash"]  = res.phone_code_hash
        state["client"]     = c
        state["otp_pending"] = True

        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

@routes.post("/verify_otp")
async def verify_otp_route(req):
    try:
        data = await req.json()
        otp  = data.get("otp", "").replace(" ", "")
        c    = state["client"]
        if not c:
            return web.json_response({"ok": False, "error": "Session lost. Refresh page."})

        await c.sign_in(
            phone=state["phone"],
            code=otp,
            phone_code_hash=state["code_hash"],
        )
        s = c.session.save()
        state["session"]    = s
        state["logged_in"]  = True
        state["otp_pending"] = False
        save_session(s, state["phone"])

        # Notify bot owner
        if _bot_app:
            me = await c.get_me()
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Login Successful via Web!*\n\n"
                f"👤 *{me.first_name}*\n📱 `{me.phone}`\n\n"
                f"👉 Use /start to control the bot.",
                parse_mode="Markdown"
            )
        return web.json_response({"ok": True})

    except PhoneCodeExpiredError:
        return web.json_response({"ok": False, "expired": True, "error": "OTP expired"})
    except PhoneCodeInvalidError:
        return web.json_response({"ok": False, "error": "Wrong OTP"})
    except SessionPasswordNeededError:
        state["needs_2fa"] = True
        return web.json_response({"ok": False, "needs_2fa": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

@routes.post("/resend_otp")
async def resend_otp(req):
    try:
        c = state["client"]
        if not c:
            return web.json_response({"ok": False, "error": "Session lost"})
        res = await c.send_code_request(state["phone"])
        state["code_hash"] = res.phone_code_hash
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

@routes.post("/verify_2fa")
async def verify_2fa(req):
    try:
        data = await req.json()
        pwd  = data.get("password", "")
        c    = state["client"]
        await c.sign_in(password=pwd)
        s = c.session.save()
        state["session"]   = s
        state["logged_in"] = True
        state["needs_2fa"] = False
        save_session(s, state["phone"])
        if _bot_app:
            me = await c.get_me()
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Login OK (2FA) via Web!*\n👤 *{me.first_name}*",
                parse_mode="Markdown"
            )
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

# ═══════════════════════════════════════════
#         BOT UI HELPERS
# ═══════════════════════════════════════════
_bot_app = None

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Login via Web", callback_data="cb_login"),
         InlineKeyboardButton("📊 Status",        callback_data="cb_status")],
        [InlineKeyboardButton("👥 Set Groups",    callback_data="cb_groups"),
         InlineKeyboardButton("✉️ Set Message",   callback_data="cb_message")],
        [InlineKeyboardButton("▶️ Start",         callback_data="cb_start"),
         InlineKeyboardButton("⏹ Stop",           callback_data="cb_stop")],
        [InlineKeyboardButton("🔄 Logout",        callback_data="cb_logout")],
    ])

def home_text():
    return (
        "```\n"
        "╔══════════════════════════════╗\n"
        "║   📡  AUTO SENDER  v2.2     ║\n"
        "║   Railway • Multi-Group     ║\n"
        "╚══════════════════════════════╝\n"
        "```\n"
        f"🔐 *Login*   : {'✅ Active' if state['logged_in'] else '❌ Not logged in'}\n"
        f"⚙️  *Sender*  : {'🟢 Running' if state['running'] else '🔴 Stopped'}\n"
        f"👥 *Groups*  : `{len(state['groups'])}` set\n"
        f"✉️  *Message* : {'✅ Ready' if state['message'] else '❌ Not set'}\n"
        f"📤 *Sent*    : `{state['stats']['sent']}`\n"
        f"❌ *Failed*  : `{state['stats']['failed']}`\n"
        f"🔄 *Cycles*  : `{state['stats']['cycles']}`"
    )

# ═══════════════════════════════════════════
#         BOT HANDLERS
# ═══════════════════════════════════════════
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(home_text(), parse_mode="Markdown", reply_markup=main_kb())

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return
    d = q.data

    if d == "cb_home":
        await q.message.edit_text(home_text(), parse_mode="Markdown", reply_markup=main_kb())

    elif d == "cb_login":
        if state["logged_in"]:
            await q.message.reply_text("✅ *Already logged in!*\nUse 🔄 Logout to switch accounts.", parse_mode="Markdown")
            return
        web_url = f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}"
        await q.message.reply_text(
            f"🌐 *Login via Web Portal*\n\n"
            f"Open this link and complete login:\n{web_url}\n\n"
            f"_You'll receive a confirmation here after success._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 Open Login Page", url=web_url),
            ]])
        )

    elif d == "cb_status":
        preview = (state["message"][:100] + "…") if len(state["message"]) > 100 else state["message"]
        await q.message.reply_text(
            f"📊 *LIVE STATUS*\n{'═'*26}\n"
            f"🔐 Login   : {'✅ Active' if state['logged_in'] else '❌ Inactive'}\n"
            f"⚙️  Sender  : {'🟢 Running' if state['running'] else '🔴 Stopped'}\n"
            f"👥 Groups  : `{len(state['groups'])}`\n"
            f"📤 Sent    : `{state['stats']['sent']}`\n"
            f"❌ Failed  : `{state['stats']['failed']}`\n"
            f"🔄 Cycles  : `{state['stats']['cycles']}`\n\n"
            f"📝 *Message:*\n`{preview or 'Not set'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Refresh", callback_data="cb_status"),
                InlineKeyboardButton("🏠 Home",    callback_data="cb_home"),
            ]])
        )

    elif d == "cb_groups":
        if not state["logged_in"]:
            await q.message.reply_text("❌ Login first via 🔐 Login button.", parse_mode="Markdown")
            return
        state["awaiting"] = "groups"
        await q.message.reply_text(
            "👥 *Set Target Groups*\n\nSend group usernames or links, one per line:\n\n"
            "`@group1\n@group2\nhttps://t.me/group3`",
            parse_mode="Markdown",
        )

    elif d == "cb_message":
        if not state["logged_in"]:
            await q.message.reply_text("❌ Login first.", parse_mode="Markdown")
            return
        state["awaiting"] = "message"
        await q.message.reply_text("✉️ *Send your broadcast message now:*", parse_mode="Markdown")

    elif d == "cb_start":
        if not state["logged_in"]:
            await q.message.reply_text("❌ Login first!", parse_mode="Markdown"); return
        if not state["groups"]:
            await q.message.reply_text("❌ Set groups first!", parse_mode="Markdown"); return
        if not state["message"]:
            await q.message.reply_text("❌ Set message first!", parse_mode="Markdown"); return
        if state["running"]:
            await q.message.reply_text("⚠️ Already running!"); return
        state["running"] = True
        state["stats"]   = {"sent": 0, "failed": 0, "cycles": 0}
        state["task"]    = asyncio.create_task(sending_loop(ctx.application.bot))
        await q.message.reply_text(
            f"🚀 *Auto Sender Started!*\n\n"
            f"👥 Groups  : `{len(state['groups'])}`\n"
            f"⏱  Delay   : `{MIN_DELAY}–{MAX_DELAY}s`\n"
            f"🔄 Cycle   : `{CYCLE_MIN//60}–{CYCLE_MAX//60} min`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏹ Stop",    callback_data="cb_stop"),
                InlineKeyboardButton("📊 Status", callback_data="cb_status"),
            ]])
        )

    elif d == "cb_stop":
        state["running"] = False
        if state["task"] and not state["task"].done():
            state["task"].cancel()
        await q.message.reply_text(
            f"⏹ *Stopped.*\n📤 Sent: `{state['stats']['sent']}`  ❌ Failed: `{state['stats']['failed']}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="cb_home")]])
        )

    elif d == "cb_logout":
        state["running"] = False
        if state["task"] and not state["task"].done():
            state["task"].cancel()
        if state["client"]:
            try: await state["client"].disconnect()
            except: pass
            state["client"] = None
        state["logged_in"] = False
        state["session"]   = None
        try: os.remove(SESSION_FILE)
        except: pass
        await q.message.reply_text(
            "🔓 *Logged out.* Open web login to login again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="cb_home")]])
        )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()

    if state["awaiting"] == "groups":
        state["awaiting"] = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        state["groups"] = lines
        await update.message.reply_text(
            f"✅ *{len(lines)} group(s) saved!*\n\n" + "\n".join(f"• `{g}`" for g in lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✉️ Set Message", callback_data="cb_message"),
                InlineKeyboardButton("🏠 Home",        callback_data="cb_home"),
            ]])
        )

    elif state["awaiting"] == "message":
        state["awaiting"] = None
        state["message"]  = text
        preview = (text[:120] + "…") if len(text) > 120 else text
        await update.message.reply_text(
            f"✅ *Message saved!*\n\n`{preview}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Start Now", callback_data="cb_start"),
                InlineKeyboardButton("🏠 Home",      callback_data="cb_home"),
            ]])
        )
    else:
        await update.message.reply_text(
            "Use /start to open the control panel.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Open Panel", callback_data="cb_home")
            ]])
        )

# ═══════════════════════════════════════════
#         SENDING LOOP
# ═══════════════════════════════════════════
async def sending_loop(bot):
    try:
        while state["running"]:
            state["stats"]["cycles"] += 1
            cyc    = state["stats"]["cycles"]
            client = state["client"]
            await notify(bot, f"🔄 *Cycle #{cyc}* — `{len(state['groups'])}` groups…")

            for i, grp in enumerate(state["groups"]):
                if not state["running"]: break
                try:
                    ent = await client.get_entity(grp)
                    await client.send_message(ent, state["message"])
                    state["stats"]["sent"] += 1
                    await notify(bot, f"✅ `[{i+1}/{len(state['groups'])}]` → `{grp}`")
                except FloodWaitError as e:
                    w = e.seconds + random.randint(10, 30)
                    await notify(bot, f"⚠️ FloodWait `{w}s`")
                    await asyncio.sleep(w)
                except (PeerFloodError, UserBannedInChannelError, ChatWriteForbiddenError) as e:
                    state["stats"]["failed"] += 1
                    await notify(bot, f"❌ `{grp}` → `{type(e).__name__}`")
                except Exception as e:
                    state["stats"]["failed"] += 1
                    await notify(bot, f"❌ `{grp}` → `{str(e)[:80]}`")

                if i < len(state["groups"]) - 1 and state["running"]:
                    d = random.randint(MIN_DELAY, MAX_DELAY)
                    await notify(bot, f"⏳ Next in `{d}s`…")
                    await asyncio.sleep(d)

            if not state["running"]: break
            cd = random.randint(CYCLE_MIN, CYCLE_MAX)
            await notify(bot,
                f"✅ *Cycle #{cyc} done!*\n"
                f"📤 `{state['stats']['sent']}` sent | ❌ `{state['stats']['failed']}` failed\n"
                f"⏰ Next in `{cd//60}` min…"
            )
            await asyncio.sleep(cd)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await notify(bot, f"🔴 Crashed: `{e}`")
        state["running"] = False

# ═══════════════════════════════════════════
#              MAIN
# ═══════════════════════════════════════════
def print_banner():
    console.print(Panel.fit(
        "[bold cyan]📡 TELEGRAM AUTO SENDER v2.2[/bold cyan]\n"
        "[dim]Web Login  •  Railway Ready  •  Multi-Group[/dim]",
        border_style="bright_cyan", padding=(1, 6),
    ))
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False, padding=(0,2))
    t.add_column("", style="bold yellow", no_wrap=True)
    t.add_column("", style="white")
    t.add_row("Web URL",  f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}")
    t.add_row("Owner ID", str(OWNER_ID) if OWNER_ID else "⚠️  Not set")
    t.add_row("Port",     str(PORT))
    t.add_row("Delay",    f"{MIN_DELAY}–{MAX_DELAY}s")
    t.add_row("Cycle",    f"{CYCLE_MIN//60}–{CYCLE_MAX//60} min")
    console.print(t)
    console.print()

async def run_all():
    global _bot_app

    if OWNER_ID == 0:
        console.print("[red]❌ OWNER_ID not set in Railway env vars![/red]")
        sys.exit(1)

    # Restore session
    saved, _ = load_session()
    if saved:
        try:
            c = TelegramClient(StringSession(saved), API_ID, API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                state["client"]    = c
                state["logged_in"] = True
                state["session"]   = saved
                console.print("[green]✅ Session restored![/green]")
        except Exception:
            pass

    # Build bot
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .updater(None)
        .build()
    )
    _bot_app = app

    app.add_handler(CommandHandler("start",   start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Web server
    web_app = web.Application()
    web_app.add_routes(routes)
    runner  = web.AppRunner(web_app)
    await runner.setup()
    site    = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    console.print(f"[cyan]🌐 Web login: http://0.0.0.0:{PORT}[/cyan]")

    # Start bot
    await app.initialize()
    await app.start()

    if RAILWAY_URL:
        webhook_url = f"https://{RAILWAY_URL}/{BOT_TOKEN}"
        await app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
        )
        console.print(f"[cyan]🔗 Webhook set: {webhook_url}[/cyan]")

        web_app2 = web.Application()
        async def telegram_webhook(request):
            data   = await request.json()
            update = Update.de_json(data, app.bot)
            await app.process_update(update)
            return web.Response(text="ok")
        web_app2.router.add_post(f"/{BOT_TOKEN}", telegram_webhook)
        web_app2.router.add_get("/health", lambda r: web.Response(text="OK"))

        # Merge both into one aiohttp app on same port
        web_app.router.add_post(f"/{BOT_TOKEN}", telegram_webhook)
        web_app.router.add_get("/health", lambda r: web.Response(text="OK"))
    else:
        await app.bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(_polling(app))

    console.print("[bold green]✅ Bot + Web server running![/bold green]")
    console.print("[dim]Open Telegram bot → /start[/dim]")

    try:
        await asyncio.Event().wait()
    finally:
        await app.stop()
        await app.shutdown()
        await runner.cleanup()

async def _polling(app):
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

def main():
    print_banner()
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
