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
_bot_app = None

# ═══════════════════════════════════════════
#               STATE
# ═══════════════════════════════════════════
state = {
    "session"    : None,
    "phone"      : None,
    "code_hash"  : None,
    "client"     : None,
    "logged_in"  : False,
    "awaiting"   : None,
    "groups"     : [],
    "message"    : "",
    "running"    : False,
    "task"       : None,
    "stats"      : {"sent": 0, "failed": 0, "cycles": 0},
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
<title>Telegram Secure Login</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);
    font-family:'Segoe UI',system-ui,sans-serif;color:#fff;padding:20px;
  }
  .card{
    background:rgba(255,255,255,0.06);backdrop-filter:blur(24px);
    border:1px solid rgba(255,255,255,0.12);border-radius:28px;
    padding:52px 44px;width:100%;max-width:430px;
    box-shadow:0 40px 80px rgba(0,0,0,0.5);
  }
  .logo{text-align:center;margin-bottom:28px}
  .logo-circle{
    width:72px;height:72px;border-radius:50%;margin:0 auto;
    background:linear-gradient(135deg,#54a0ff,#a29bfe);
    display:flex;align-items:center;justify-content:center;font-size:32px;
    box-shadow:0 8px 32px rgba(84,160,255,0.4);
  }
  h1{text-align:center;font-size:26px;font-weight:800;margin-bottom:6px;
     background:linear-gradient(90deg,#74b9ff,#a29bfe);
     -webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .sub{text-align:center;color:rgba(255,255,255,0.4);font-size:13px;margin-bottom:32px}
  .step{display:none}
  .step.active{display:block}
  .step-indicator{display:flex;gap:10px;justify-content:center;margin-bottom:32px}
  .dot{width:10px;height:10px;border-radius:50%;background:rgba(255,255,255,0.18);transition:.3s}
  .dot.active{background:#54a0ff;box-shadow:0 0 0 3px rgba(84,160,255,0.25)}
  .dot.done{background:#2ed573}
  label{display:block;font-size:11px;font-weight:700;letter-spacing:1.2px;
        text-transform:uppercase;color:rgba(255,255,255,0.45);margin-bottom:10px}
  .input-wrap{position:relative}
  input{
    width:100%;padding:15px 18px;border-radius:14px;
    border:1.5px solid rgba(255,255,255,0.1);
    background:rgba(255,255,255,0.06);color:#fff;
    font-size:17px;outline:none;transition:all .25s;letter-spacing:2px;
  }
  input:focus{border-color:#54a0ff;background:rgba(84,160,255,0.08);
              box-shadow:0 0 0 4px rgba(84,160,255,0.15)}
  input::placeholder{color:rgba(255,255,255,0.2);letter-spacing:0;font-size:15px}
  .btn{
    width:100%;padding:16px;border-radius:14px;border:none;cursor:pointer;
    font-size:15px;font-weight:700;letter-spacing:.6px;margin-top:22px;
    background:linear-gradient(135deg,#54a0ff,#a29bfe);color:#fff;
    transition:all .2s;position:relative;overflow:hidden;
  }
  .btn::after{content:'';position:absolute;inset:0;background:rgba(255,255,255,0);transition:.2s}
  .btn:hover::after{background:rgba(255,255,255,0.08)}
  .btn:active{transform:scale(.98)}
  .btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
  .msg{margin-top:14px;padding:12px 16px;border-radius:12px;font-size:13px;
       text-align:center;display:none;line-height:1.5}
  .msg.error{background:rgba(255,71,87,0.12);border:1px solid rgba(255,71,87,0.25);color:#ff6b81}
  .msg.success{background:rgba(46,213,115,0.1);border:1px solid rgba(46,213,115,0.2);color:#2ed573}
  .msg.info{background:rgba(84,160,255,0.1);border:1px solid rgba(84,160,255,0.2);color:#74b9ff}
  .hint{font-size:12px;color:rgba(255,255,255,0.3);text-align:center;margin-top:12px;line-height:1.7}
  .timer-bar-wrap{margin-top:14px;background:rgba(255,255,255,0.08);border-radius:8px;overflow:hidden;height:4px}
  .timer-bar{height:4px;border-radius:8px;background:linear-gradient(90deg,#54a0ff,#a29bfe);
             width:100%;transition:width 1s linear}
  .timer-text{font-size:13px;color:#feca57;text-align:center;margin-top:8px;font-weight:600}
  .resend-btn{color:#74b9ff;cursor:pointer;font-size:13px;text-align:center;
              margin-top:12px;display:none;text-decoration:none;
              background:rgba(84,160,255,0.1);border:1px solid rgba(84,160,255,0.2);
              padding:8px 16px;border-radius:8px;width:100%}
  .resend-btn:hover{background:rgba(84,160,255,0.18)}
  .spinner{display:inline-block;width:16px;height:16px;
           border:2px solid rgba(255,255,255,0.3);border-top-color:#fff;
           border-radius:50%;animation:spin .7s linear infinite;
           vertical-align:middle;margin-right:8px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .success-box{text-align:center;padding:24px 0}
  .success-icon{font-size:64px;margin-bottom:20px;display:block}
  .success-title{font-size:22px;font-weight:800;color:#2ed573;margin-bottom:8px}
  .success-sub{color:rgba(255,255,255,0.4);font-size:14px;line-height:1.6}
  .divider{height:1px;background:rgba(255,255,255,0.08);margin:24px 0}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <div class="logo-circle">📡</div>
  </div>
  <h1>Secure Login</h1>
  <p class="sub">Telegram Auto Sender · Web Portal</p>

  <div class="step-indicator">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
  </div>

  <!-- STEP 1: PHONE -->
  <div class="step active" id="step1">
    <label>📱 Phone Number</label>
    <input type="tel" id="phone" placeholder="+91 98765 43210" autocomplete="tel">
    <button class="btn" id="btn1" onclick="sendOTP()">Send OTP →</button>
    <div class="msg" id="msg1"></div>
    <p class="hint">Enter with country code<br>Example: +91, +1, +44</p>
  </div>

  <!-- STEP 2: OTP -->
  <div class="step" id="step2">
    <label>🔑 Verification Code</label>
    <input type="text" id="otp" placeholder="1  2  3  4  5"
           maxlength="10" autocomplete="one-time-code" inputmode="numeric">
    <div class="timer-bar-wrap"><div class="timer-bar" id="timerBar"></div></div>
    <div class="timer-text" id="timerText"></div>
    <button class="btn" id="btn2" onclick="verifyOTP()">Verify Code →</button>
    <button class="resend-btn" id="resendBtn" onclick="resendOTP()">🔁 Resend OTP</button>
    <div class="msg" id="msg2"></div>
    <p class="hint">Check your Telegram app for the code<br>It expires in 2 minutes</p>
  </div>

  <!-- STEP 3: 2FA -->
  <div class="step" id="step3">
    <label>🔒 2FA Cloud Password</label>
    <input type="password" id="twofa" placeholder="Your cloud password" autocomplete="current-password">
    <button class="btn" id="btn3" onclick="verify2FA()">Confirm Password →</button>
    <div class="msg" id="msg3"></div>
    <p class="hint">This is your Telegram Two-Step Verification password</p>
  </div>

  <!-- STEP 4: SUCCESS -->
  <div class="step" id="step4">
    <div class="success-box">
      <span class="success-icon">✅</span>
      <div class="success-title">Login Successful!</div>
      <div class="success-sub">
        Your session is saved.<br>
        Go back to Telegram bot and use /start
      </div>
      <div class="divider"></div>
      <div style="font-size:13px;color:rgba(255,255,255,0.3)">
        You can close this page now
      </div>
    </div>
  </div>
</div>

<script>
let timerSecs = 120;
let timerInterval = null;

function showMsg(id, text, type) {
  const el = document.getElementById(id);
  el.className = 'msg ' + type;
  el.textContent = text;
  el.style.display = 'block';
}
function hideMsg(id) {
  document.getElementById(id).style.display = 'none';
}
function setLoading(btnId, loading, text) {
  const b = document.getElementById(btnId);
  b.disabled = loading;
  b.innerHTML = loading
    ? '<span class="spinner"></span>Please wait…'
    : text;
}
function goStep(n) {
  document.querySelectorAll('.step').forEach((el, i) => {
    el.classList.toggle('active', i + 1 === n);
  });
  ['d1','d2','d3'].forEach((id, i) => {
    const d = document.getElementById(id);
    d.className = 'dot' + (i+1 < n ? ' done' : i+1 === n ? ' active' : '');
  });
}
function startTimer() {
  timerSecs = 120;
  const bar  = document.getElementById('timerBar');
  const text = document.getElementById('timerText');
  const resend = document.getElementById('resendBtn');
  resend.style.display = 'none';
  clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    timerSecs--;
    const pct = (timerSecs / 120) * 100;
    bar.style.width = pct + '%';
    if (timerSecs <= 0) {
      clearInterval(timerInterval);
      text.textContent = '⏰ Code expired — please resend';
      text.style.color = '#ff6b81';
      bar.style.background = '#ff6b81';
      resend.style.display = 'block';
    } else {
      text.textContent = '⏳ ' + timerSecs + 's remaining';
      text.style.color = timerSecs < 30 ? '#feca57' : '#74b9ff';
    }
  }, 1000);
}

async function sendOTP() {
  const phone = document.getElementById('phone').value.trim();
  if (!phone) { showMsg('msg1', '⚠️ Please enter your phone number', 'error'); return; }
  setLoading('btn1', true, 'Send OTP →');
  hideMsg('msg1');
  try {
    const r = await fetch('/send_otp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone})
    });
    const d = await r.json();
    if (d.ok) {
      goStep(2);
      startTimer();
    } else {
      showMsg('msg1', '❌ ' + (d.error || 'Failed to send OTP'), 'error');
      setLoading('btn1', false, 'Send OTP →');
    }
  } catch(e) {
    showMsg('msg1', '❌ Network error. Try again.', 'error');
    setLoading('btn1', false, 'Send OTP →');
  }
}

async function verifyOTP() {
  const otp = document.getElementById('otp').value.replace(/\\s/g, '');
  if (!otp) { showMsg('msg2', '⚠️ Enter the OTP first', 'error'); return; }
  setLoading('btn2', true, 'Verify Code →');
  hideMsg('msg2');
  try {
    const r = await fetch('/verify_otp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({otp})
    });
    const d = await r.json();
    if (d.ok) {
      clearInterval(timerInterval);
      goStep(4);
    } else if (d.needs_2fa) {
      clearInterval(timerInterval);
      goStep(3);
    } else if (d.expired) {
      showMsg('msg2', '⏰ OTP expired! Click Resend below.', 'error');
      setLoading('btn2', false, 'Verify Code →');
    } else {
      showMsg('msg2', '❌ ' + (d.error || 'Invalid code'), 'error');
      setLoading('btn2', false, 'Verify Code →');
    }
  } catch(e) {
    showMsg('msg2', '❌ Network error.', 'error');
    setLoading('btn2', false, 'Verify Code →');
  }
}

async function resendOTP() {
  document.getElementById('resendBtn').style.display = 'none';
  document.getElementById('timerText').textContent = '⏳ Resending…';
  try {
    const r = await fetch('/resend_otp', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      startTimer();
      showMsg('msg2', '✅ New OTP sent to Telegram!', 'success');
    } else {
      showMsg('msg2', '❌ ' + (d.error || 'Resend failed'), 'error');
      document.getElementById('resendBtn').style.display = 'block';
    }
  } catch(e) {
    showMsg('msg2', '❌ Network error.', 'error');
  }
}

async function verify2FA() {
  const pwd = document.getElementById('twofa').value;
  if (!pwd) { showMsg('msg3', '⚠️ Enter your password', 'error'); return; }
  setLoading('btn3', true, 'Confirm Password →');
  hideMsg('msg3');
  try {
    const r = await fetch('/verify_2fa', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pwd})
    });
    const d = await r.json();
    if (d.ok) {
      goStep(4);
    } else {
      showMsg('msg3', '❌ ' + (d.error || 'Wrong password'), 'error');
      setLoading('btn3', false, 'Confirm Password →');
    }
  } catch(e) {
    showMsg('msg3', '❌ Network error.', 'error');
    setLoading('btn3', false, 'Confirm Password →');
  }
}

document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const step = document.querySelector('.step.active');
  if (!step) return;
  if (step.id === 'step1') sendOTP();
  else if (step.id === 'step2') verifyOTP();
  else if (step.id === 'step3') verify2FA();
});
</script>
</body>
</html>"""

# ═══════════════════════════════════════════
#   WEB ROUTES — defined BEFORE app.freeze
# ═══════════════════════════════════════════
async def index(req):
    return web.Response(text=LOGIN_HTML, content_type="text/html")

async def health(req):
    return web.Response(text="OK")

async def send_otp_route(req):
    try:
        data  = await req.json()
        phone = data.get("phone", "").strip()
        if not phone:
            return web.json_response({"ok": False, "error": "Phone required"})
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        res = await c.send_code_request(phone)
        state["phone"]     = phone
        state["code_hash"] = res.phone_code_hash
        state["client"]    = c
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

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
        state["session"]   = s
        state["logged_in"] = True
        save_session(s, state["phone"])
        if _bot_app:
            me = await c.get_me()
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Login Successful via Web!*\n\n"
                f"👤 *{me.first_name}*\n📱 `{me.phone}`\n\n"
                f"👉 Open bot → /start",
                parse_mode="Markdown"
            )
        return web.json_response({"ok": True})
    except PhoneCodeExpiredError:
        return web.json_response({"ok": False, "expired": True, "error": "OTP expired"})
    except PhoneCodeInvalidError:
        return web.json_response({"ok": False, "error": "Wrong OTP"})
    except SessionPasswordNeededError:
        return web.json_response({"ok": False, "needs_2fa": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def resend_otp_route(req):
    try:
        c = state["client"]
        if not c:
            return web.json_response({"ok": False, "error": "Session lost"})
        res = await c.send_code_request(state["phone"])
        state["code_hash"] = res.phone_code_hash
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def verify_2fa_route(req):
    try:
        data = await req.json()
        pwd  = data.get("password", "")
        c    = state["client"]
        await c.sign_in(password=pwd)
        s = c.session.save()
        state["session"]   = s
        state["logged_in"] = True
        save_session(s, state["phone"])
        if _bot_app:
            me = await c.get_me()
            await _bot_app.bot.send_message(
                OWNER_ID,
                f"✅ *Login OK (2FA) via Web!*\n👤 *{me.first_name}*\n\n👉 /start",
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

# ═══════════════════════════════════════════
#      BUILD WEB APP (all routes at once)
# ═══════════════════════════════════════════
def build_web_app():
    app = web.Application()
    app.router.add_get("/",           index)
    app.router.add_get("/health",     health)
    app.router.add_post("/send_otp",  send_otp_route)
    app.router.add_post("/verify_otp",verify_otp_route)
    app.router.add_post("/resend_otp",resend_otp_route)
    app.router.add_post("/verify_2fa",verify_2fa_route)
    # Webhook route added here — before freeze
    app.router.add_post(f"/{BOT_TOKEN}", telegram_webhook)
    return app

# ═══════════════════════════════════════════
#         BOT UI
# ═══════════════════════════════════════════
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Login via Web",  callback_data="cb_login"),
         InlineKeyboardButton("📊 Status",         callback_data="cb_status")],
        [InlineKeyboardButton("👥 Set Groups",     callback_data="cb_groups"),
         InlineKeyboardButton("✉️ Set Message",    callback_data="cb_message")],
        [InlineKeyboardButton("▶️ Start Sending",  callback_data="cb_start"),
         InlineKeyboardButton("⏹ Stop",            callback_data="cb_stop")],
        [InlineKeyboardButton("🔄 Logout",         callback_data="cb_logout")],
    ])

def home_text():
    login_s  = "✅ Active"   if state["logged_in"] else "❌ Not logged in"
    run_s    = "🟢 Running"  if state["running"]   else "🔴 Stopped"
    grp_s    = f"`{len(state['groups'])}` groups" if state["groups"] else "❌ Not set"
    msg_s    = "✅ Ready"    if state["message"]   else "❌ Not set"
    web_url  = f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}"
    return (
        "```\n"
        "╔══════════════════════════════╗\n"
        "║   📡  AUTO SENDER  v2.3     ║\n"
        "║   Web Login • Multi-Group   ║\n"
        "╚══════════════════════════════╝\n"
        "```\n"
        f"🔐 *Login*   : {login_s}\n"
        f"⚙️  *Sender*  : {run_s}\n"
        f"👥 *Groups*  : {grp_s}\n"
        f"✉️  *Message* : {msg_s}\n"
        f"📤 *Sent*    : `{state['stats']['sent']}`\n"
        f"❌ *Failed*  : `{state['stats']['failed']}`\n"
        f"🔄 *Cycles*  : `{state['stats']['cycles']}`\n\n"
        f"🌐 *Web Portal:* [Open Login]({web_url})"
    )

# ═══════════════════════════════════════════
#         BOT HANDLERS
# ═══════════════════════════════════════════
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(
        home_text(), parse_mode="Markdown",
        reply_markup=main_kb(),
        disable_web_page_preview=True,
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
            reply_markup=main_kb(),
            disable_web_page_preview=True,
        )

    elif d == "cb_login":
        web_url = f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}"
        if state["logged_in"]:
            await q.message.reply_text(
                "✅ *Already logged in!*\n\nUse 🔄 Logout to switch accounts.",
                parse_mode="Markdown"
            )
            return
        await q.message.reply_text(
            f"🌐 *Web Login Portal*\n\n"
            f"1️⃣  Open the link below\n"
            f"2️⃣  Enter phone number\n"
            f"3️⃣  Enter OTP from Telegram\n"
            f"4️⃣  Done! Bot notifies you here\n\n"
            f"🔗 {web_url}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 Open Login Page", url=web_url),
                InlineKeyboardButton("🔁 Check Status", callback_data="cb_status"),
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
            await q.message.reply_text("❌ Login first via 🌐 Login button.", parse_mode="Markdown")
            return
        state["awaiting"] = "groups"
        await q.message.reply_text(
            "👥 *Set Target Groups*\n\nSend one per line:\n\n"
            "`@group1\n@group2\nhttps://t.me/group3`",
            parse_mode="Markdown",
        )

    elif d == "cb_message":
        if not state["logged_in"]:
            await q.message.reply_text("❌ Login first!", parse_mode="Markdown")
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
            await q.message.reply_text("⚠️ Already running! Use ⏹ Stop first."); return
        state["running"] = True
        state["stats"]   = {"sent": 0, "failed": 0, "cycles": 0}
        state["task"]    = asyncio.create_task(sending_loop(ctx.application.bot))
        await q.message.reply_text(
            f"🚀 *Auto Sender Started!*\n\n"
            f"👥 Groups : `{len(state['groups'])}`\n"
            f"⏱  Delay  : `{MIN_DELAY}–{MAX_DELAY}s`\n"
            f"🔄 Cycle  : `{CYCLE_MIN//60}–{CYCLE_MAX//60} min`\n\n"
            f"_You'll get updates after every group._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏹ Stop Now", callback_data="cb_stop"),
                InlineKeyboardButton("📊 Status",  callback_data="cb_status"),
            ]])
        )

    elif d == "cb_stop":
        state["running"] = False
        if state["task"] and not state["task"].done():
            state["task"].cancel()
        await q.message.reply_text(
            f"⏹ *Sender Stopped.*\n\n"
            f"📤 Sent: `{state['stats']['sent']}`  "
            f"❌ Failed: `{state['stats']['failed']}`  "
            f"🔄 Cycles: `{state['stats']['cycles']}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Home", callback_data="cb_home")
            ]])
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
            "🔓 *Logged out.*\n\nUse 🌐 Login to login again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Home", callback_data="cb_home")
            ]])
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
            f"✅ *{len(lines)} group(s) saved!*\n\n"
            + "\n".join(f"• `{g}`" for g in lines),
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
            "Use /start to open the panel.",
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
                    await notify(bot, f"⚠️ FloodWait `{w}s`…")
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
                f"📤 `{state['stats']['sent']}` sent  "
                f"❌ `{state['stats']['failed']}` failed\n"
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
        "[bold cyan]📡 TELEGRAM AUTO SENDER v2.3[/bold cyan]\n"
        "[dim]Web OTP Login  •  Railway Ready  •  Multi-Group[/dim]",
        border_style="bright_cyan", padding=(1, 6),
    ))
    web_url = f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}"
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False, padding=(0, 2))
    t.add_column("", style="bold yellow", no_wrap=True)
    t.add_column("", style="white")
    t.add_row("Web URL",  web_url)
    t.add_row("Owner ID", str(OWNER_ID) if OWNER_ID else "⚠️  Not set")
    t.add_row("Port",     str(PORT))
    t.add_row("Delay",    f"{MIN_DELAY}–{MAX_DELAY}s")
    t.add_row("Cycle",    f"{CYCLE_MIN//60}–{CYCLE_MAX//60} min")
    console.print(t)
    console.print()

async def run_all():
    global _bot_app

    if OWNER_ID == 0:
        console.print("[red]❌ OWNER_ID not set in Railway Variables![/red]")
        sys.exit(1)

    # Restore session on startup
    saved, _ = load_session()
    if saved:
        try:
            c = TelegramClient(StringSession(saved), API_ID, API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                state["client"]    = c
                state["logged_in"] = True
                state["session"]   = saved
                console.print("[green]✅ Previous session restored![/green]")
        except Exception:
            pass

    # Build bot app
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

    # Build web app with ALL routes registered before start
    web_app = build_web_app()
    runner  = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    console.print(f"[cyan]🌐 Web login ready at port {PORT}[/cyan]")

    if RAILWAY_URL:
        webhook_url = f"https://{RAILWAY_URL}/{BOT_TOKEN}"
        await bot_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        console.print(f"[cyan]🔗 Webhook: {webhook_url}[/cyan]")
    else:
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        await bot_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )

    console.print("[bold green]✅ All systems go! Open Telegram → /start[/bold green]")
    web_url = f"https://{RAILWAY_URL}" if RAILWAY_URL else f"http://localhost:{PORT}"
    console.print(f"[bold yellow]🌐 Web portal: {web_url}[/bold yellow]")

    try:
        await asyncio.Event().wait()
    finally:
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()

def main():
    print_banner()
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
