# 📡 Telegram Auto Sender v2.3

## 🚀 Render Deployment

### Step 1 — Push to GitHub
Upload all files to a GitHub repo.

### Step 2 — Create Render Web Service
1. Go to https://render.com
2. New → Web Service → Connect GitHub repo
3. Set:
   - **Build Command:** `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command:** `python main.py`

### Step 3 — Set Environment Variables
| Variable | Value |
|---|---|
| `OWNER_ID` | Your Telegram numeric user ID (get from @userinfobot) |
| `BOT_TOKEN` | Your bot token from @BotFather |
| `SESSION_STRING` | (Optional) Paste session string for persistent login |

### Step 4 — Deploy!
Render gives you a URL like: `https://tg-auto-sender.onrender.com`

---

## 🤖 How to Use

1. Open Telegram bot → `/start`
2. Press **🌐 Login via Web**
3. Open the web link → Enter phone → Enter OTP → Done!
4. Press **👥 Set Groups** → send group links one per line
5. Press **✉️ Set Message** → send your message
6. Press **▶️ Start Sending** 🚀

---

## 📋 Bot Commands
| Command | Action |
|---|---|
| `/start` | Open control panel |

All controls are via inline buttons — no other commands needed!
