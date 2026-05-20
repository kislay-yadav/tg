# 📡 AUTO SENDER PRO — Multi-User Edition

## 🚀 Render Deploy Steps

1. Upload all files to GitHub repo
2. Render → New → Web Service → Connect repo
3. Build Command: `pip install --upgrade pip && pip install -r requirements.txt`
4. Start Command: `python main.py`
5. Add Environment Variables:
   - `OWNER_ID` = Your Telegram numeric ID (from @userinfobot)
   - `BOT_TOKEN` = Your bot token

## 🌐 How It Works

### URLs
- `https://your-app.onrender.com/` → Main landing page
- `https://your-app.onrender.com/register` → User login page
- `https://your-app.onrender.com/{slug}` → Individual user page

### Flow
1. Owner opens bot → /start → sees admin panel
2. Owner shares register link with users
3. Users open link → enter name + phone → OTP → session created
4. Owner gets notified of each new user
5. Owner can set groups/message per user and start/stop individually
6. All sessions auto-restore if Render restarts

## 🔒 Security
- AES-256 session encryption
- Sessions stored server-side only
- Users never see each others data
- Owner has full control via bot

## ⚡ Anti-Sleep
Keep-alive ping runs every 14 minutes to prevent Render hibernation.
