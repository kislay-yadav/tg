"""
╔══════════════════════════════════════════════════════════╗
║         TELEGRAM AUTO MESSAGE SENDER v2.0               ║
║         Bot-Based Login | Cloud Ready | Multi-Group     ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import random
import json
import logging
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PeerFloodError, UserBannedInChannelError,
    ChatWriteForbiddenError, SessionPasswordNeededError,
    PhoneCodeExpiredError, PhoneCodeInvalidError
)
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# ─────────────────────────────────────────────
#              CONFIGURATION
# ─────────────────────────────────────────────

API_ID       = int(os.environ.get("API_ID", "21952127"))
API_HASH     = os.environ.get("API_HASH", "e0a3741bb3b132947d86d8fc6218eebe")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8821493954:AAFq_3lBHYUahOanJgIVcV3HielzuPPLRTk")
OWNER_ID     = int(os.environ.get("OWNER_ID", "8156053366"))  # Your Telegram numeric ID
SESSION_ENV = "session_data.json"

# Delay between each group message (seconds) - random range to avoid detection
MIN_DELAY = 400  # minimum seconds between groups
MAX_DELAY = 1000  # maximum seconds between groups

# Delay between full cycles (seconds)
CYCLE_DELAY_MIN = 3600  # 1 hour
CYCLE_DELAY_MAX = 7200  # 2 hours


# ─────────────────────────────────────────────
#              LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

console = Console()

# ─────────────────────────────────────────────
#              STATE
# ─────────────────────────────────────────────

state = {
    "session_string" : SESSION_ENV or None,
    "phone"          : None,
    "groups"         : [],
    "message"        : "",
    "running"        : False,
    "logged_in"      : False,
    "awaiting"       : None,
    "phone_code_hash": None,
    "user_client"    : None,
    "send_task"      : None,
    "stats"          : {"sent": 0, "failed": 0, "cycles": 0},
}

# ─────────────────────────────────────────────
#           SESSION PERSISTENCE
# ─────────────────────────────────────────────

def save_session(session_string, phone):
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump({"session": session_string, "phone": phone}, f)
    except Exception:
        pass

def load_session():
    if SESSION_ENV:
        return SESSION_ENV, None
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f:
                data = json.load(f)
                return data.get("session"), data.get("phone")
        except Exception:
            pass
    return None, None

# ─────────────────────────────────────────────
#              HELPER
# ─────────────────────────────────────────────

async def notify(bot, text):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="Markdown")
    except Exception:
        pass

# ─────────────────────────────────────────────
#           BOT COMMAND HANDLERS
# ─────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    menu = (
        "╔══════════════════════════╗\n"
        "║  📡 AUTO SENDER PANEL   ║\n"
        "╚══════════════════════════╝\n\n"
        "📋 *Commands:*\n\n"
        "🔐 /login — Login to Telegram\n"
        "👥 /setgroups — Set target groups\n"
        "✉️ /setmessage — Set broadcast message\n"
        "▶️ /startbot — Start auto sending\n"
        "⏹ /stopbot — Stop sending\n"
        "📊 /status — View stats\n"
        "🔄 /logout — Logout session\n"
    )
    await update.message.reply_text(menu, parse_mode="Markdown")


async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    session_str, phone = load_session()
    if session_str:
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                state["user_client"]    = client
                state["logged_in"]      = True
                state["session_string"] = session_str
                me = await client.get_me()
                await update.message.reply_text(
                    f"✅ *Already logged in!*\n\n"
                    f"👤 *{me.first_name}*  |  📱 `{me.phone}`\n\n"
                    f"Use /setgroups to continue.",
                    parse_mode="Markdown"
                )
                return
            await client.disconnect()
        except Exception:
            pass

    state["awaiting"] = "phone"
    await update.message.reply_text(
        "📱 *Login to Telegram*\n\n"
        "Send your phone number with country code:\n"
        "Example: `+919876543210`",
        parse_mode="Markdown"
    )


async def setgroups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not state["logged_in"]:
        await update.message.reply_text("❌ Please /login first.")
        return
    state["awaiting"] = "groups"
    await update.message.reply_text(
        "👥 *Set Target Groups*\n\n"
        "Send group usernames or links, one per line:\n\n"
        "`@group1\n@group2\nhttps://t.me/group3`",
        parse_mode="Markdown"
    )


async def setmessage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not state["logged_in"]:
        await update.message.reply_text("❌ Please /login first.")
        return
    state["awaiting"] = "message"
    await update.message.reply_text(
        "✉️ *Set Broadcast Message*\n\n"
        "Send the message to broadcast.\nEmojis supported.",
        parse_mode="Markdown"
    )


async def startbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not state["logged_in"]:
        await update.message.reply_text("❌ Please /login first.")
        return
    if not state["groups"]:
        await update.message.reply_text("❌ No groups set. Use /setgroups.")
        return
    if not state["message"]:
        await update.message.reply_text("❌ No message set. Use /setmessage.")
        return
    if state["running"]:
        await update.message.reply_text("⚠️ Already running! Use /stopbot first.")
        return

    state["running"] = True
    state["stats"]   = {"sent": 0, "failed": 0, "cycles": 0}
    bot = context.application.bot
    state["send_task"] = asyncio.create_task(sending_loop(bot))

    await update.message.reply_text(
        f"▶️ *Auto Sender Started!*\n\n"
        f"👥 Groups: `{len(state['groups'])}`\n"
        f"⏱ Delay: `{MIN_DELAY}–{MAX_DELAY}s` between groups\n"
        f"🔄 Cycle: `{CYCLE_DELAY_MIN//60}–{CYCLE_DELAY_MAX//60} min`\n\n"
        f"Use /stopbot to stop.",
        parse_mode="Markdown"
    )


async def stopbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    state["running"] = False
    if state["send_task"] and not state["send_task"].done():
        state["send_task"].cancel()
    await update.message.reply_text(
        f"⏹ *Stopped.*\n\n"
        f"📤 Sent: `{state['stats']['sent']}`\n"
        f"❌ Failed: `{state['stats']['failed']}`\n"
        f"🔄 Cycles: `{state['stats']['cycles']}`",
        parse_mode="Markdown"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    status  = "🟢 Running" if state["running"]    else "🔴 Stopped"
    login   = "✅ Logged In" if state["logged_in"] else "❌ Not Logged In"
    preview = (state["message"][:80] + "…") if len(state["message"]) > 80 else state["message"]
    await update.message.reply_text(
        f"📊 *STATUS*\n{'─'*28}\n"
        f"🔐 Login  : {login}\n"
        f"⚙️ Sender : {status}\n"
        f"👥 Groups : `{len(state['groups'])}`\n"
        f"📤 Sent   : `{state['stats']['sent']}`\n"
        f"❌ Failed : `{state['stats']['failed']}`\n"
        f"🔄 Cycles : `{state['stats']['cycles']}`\n\n"
        f"📝 *Message:*\n`{preview or 'Not set'}`",
        parse_mode="Markdown"
    )


async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    state["running"] = False
    if state["send_task"] and not state["send_task"].done():
        state["send_task"].cancel()
    state["logged_in"]      = False
    state["session_string"] = None
    if state["user_client"]:
        try:
            await state["user_client"].disconnect()
        except Exception:
            pass
        state["user_client"] = None
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    await update.message.reply_text("🔄 *Logged out.* Use /login again.", parse_mode="Markdown")

# ─────────────────────────────────────────────
#           MESSAGE HANDLER (FSM)
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = update.message.text.strip()

    if state["awaiting"] == "phone":
        state["phone"]    = text
        state["awaiting"] = None
        await update.message.reply_text("⏳ Requesting OTP from Telegram…")
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            result                   = await client.send_code_request(text)
            state["phone_code_hash"] = result.phone_code_hash
            state["user_client"]     = client
            state["awaiting"]        = "otp"
            await update.message.reply_text(
                "📲 *OTP Sent!*\n\n"
                "Enter the code received on Telegram.\n"
                "⚠️ *Enter quickly — expires in ~2 min!*\n\n"
                "Format: `12345`",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ *OTP request failed:*\n`{e}`\n\nTry /login again.", parse_mode="Markdown")

    elif state["awaiting"] == "otp":
        otp               = text.replace(" ", "").replace("-", "")
        state["awaiting"] = None
        client            = state["user_client"]
        try:
            await client.sign_in(
                phone=state["phone"],
                code=otp,
                phone_code_hash=state["phone_code_hash"],
            )
            session_str             = client.session.save()
            state["session_string"] = session_str
            state["logged_in"]      = True
            save_session(session_str, state["phone"])
            me = await client.get_me()
            await update.message.reply_text(
                f"✅ *Login Successful!*\n\n"
                f"👤 *{me.first_name}*  |  📱 `{me.phone}`\n\n"
                f"👉 Use /setgroups next.",
                parse_mode="Markdown"
            )

        except PhoneCodeExpiredError:
            await update.message.reply_text("⏰ *OTP Expired!* Requesting a new one…")
            try:
                result                   = await client.send_code_request(state["phone"])
                state["phone_code_hash"] = result.phone_code_hash
                state["awaiting"]        = "otp"
                await update.message.reply_text(
                    "📲 *New OTP Sent!* Enter it quickly:\n`12345`",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Resend failed: `{e}`\n\nTry /login.", parse_mode="Markdown")

        except PhoneCodeInvalidError:
            state["awaiting"] = "otp"
            await update.message.reply_text("❌ *Wrong OTP!* Please resend the correct code:", parse_mode="Markdown")

        except SessionPasswordNeededError:
            state["awaiting"] = "password"
            await update.message.reply_text("🔒 *2FA Required*\n\nSend your password:", parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ *Error:*\n`{e}`\n\nTry /login again.", parse_mode="Markdown")

    elif state["awaiting"] == "password":
        state["awaiting"] = None
        client            = state["user_client"]
        try:
            await client.sign_in(password=text)
            session_str             = client.session.save()
            state["session_string"] = session_str
            state["logged_in"]      = True
            save_session(session_str, state["phone"])
            me = await client.get_me()
            await update.message.reply_text(
                f"✅ *Login Successful (2FA)!*\n\n👤 *{me.first_name}*\n\n👉 Use /setgroups next.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ *2FA Error:*\n`{e}`\n\nTry /login.", parse_mode="Markdown")

    elif state["awaiting"] == "groups":
        state["awaiting"] = None
        lines             = [l.strip() for l in text.splitlines() if l.strip()]
        state["groups"]   = lines
        group_list        = "\n".join(f"  `{g}`" for g in lines)
        await update.message.reply_text(
            f"✅ *{len(lines)} Group(s) Set:*\n\n{group_list}\n\n👉 Use /setmessage next.",
            parse_mode="Markdown"
        )

    elif state["awaiting"] == "message":
        state["awaiting"] = None
        state["message"]  = text
        preview = (text[:100] + "…") if len(text) > 100 else text
        await update.message.reply_text(
            f"✅ *Message Set!*\n\n`{preview}`\n\n👉 Use /startbot to begin.",
            parse_mode="Markdown"
        )

    else:
        await update.message.reply_text("ℹ️ Use /start to see commands.")

# ─────────────────────────────────────────────
#           AUTO SENDING LOOP
# ─────────────────────────────────────────────

async def sending_loop(bot):
    try:
        while state["running"]:
            state["stats"]["cycles"] += 1
            cycle  = state["stats"]["cycles"]
            client = state["user_client"]

            await notify(bot,
                f"🔄 *Cycle #{cycle} Started*\n"
                f"📤 Sending to `{len(state['groups'])}` group(s)…"
            )

            for i, group in enumerate(state["groups"]):
                if not state["running"]:
                    break
                try:
                    entity = await client.get_entity(group)
                    await client.send_message(entity, state["message"])
                    state["stats"]["sent"] += 1
                    await notify(bot, f"✅ `[{i+1}/{len(state['groups'])}]` → `{group}`")

                except FloodWaitError as e:
                    wait = e.seconds + random.randint(10, 30)
                    await notify(bot, f"⚠️ FloodWait `{wait}s`…")
                    await asyncio.sleep(wait)

                except (PeerFloodError, UserBannedInChannelError, ChatWriteForbiddenError) as e:
                    state["stats"]["failed"] += 1
                    await notify(bot, f"❌ `{group}` → `{type(e).__name__}`")

                except Exception as e:
                    state["stats"]["failed"] += 1
                    await notify(bot, f"❌ `{group}` → `{str(e)[:80]}`")

                if i < len(state["groups"]) - 1 and state["running"]:
                    delay = random.randint(MIN_DELAY, MAX_DELAY)
                    await notify(bot, f"⏳ Next group in `{delay}s`…")
                    await asyncio.sleep(delay)

            if not state["running"]:
                break

            cycle_delay = random.randint(CYCLE_DELAY_MIN, CYCLE_DELAY_MAX)
            await notify(bot,
                f"✅ *Cycle #{cycle} Done!*\n\n"
                f"📤 Sent: `{state['stats']['sent']}`\n"
                f"❌ Failed: `{state['stats']['failed']}`\n"
                f"⏰ Next in `{cycle_delay//60}` min…"
            )
            await asyncio.sleep(cycle_delay)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await notify(bot, f"🔴 *Sender crashed:* `{e}`")
        state["running"] = False

# ─────────────────────────────────────────────
#                   MAIN
# ─────────────────────────────────────────────

def print_banner():
    console.print(Panel.fit(
        "[bold cyan]📡 TELEGRAM AUTO SENDER v2.0[/bold cyan]\n"
        "[dim]Bot-Based Login  •  Cloud Ready  •  Multi-Group[/dim]",
        border_style="cyan", padding=(1, 4)
    ))
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False)
    t.add_column("K", style="bold yellow")
    t.add_column("V", style="white")
    t.add_row("Bot Token", BOT_TOKEN[:24] + "…")
    t.add_row("Owner ID",  str(OWNER_ID))
    t.add_row("API ID",    str(API_ID))
    t.add_row("Delay",     f"{MIN_DELAY}–{MAX_DELAY}s")
    t.add_row("Cycle",     f"{CYCLE_DELAY_MIN//60}–{CYCLE_DELAY_MAX//60} min")
    console.print(t)
    console.print()


def main():
    print_banner()

    if API_ID == 0 or not API_HASH:
        console.print("[bold red]❌ API_ID or API_HASH missing! Set as environment variables.[/bold red]")
        sys.exit(1)
    if OWNER_ID == 0:
        console.print("[bold red]❌ OWNER_ID missing! Set as environment variable.[/bold red]")
        sys.exit(1)

    console.print("[bold green]✅ Starting bot…[/bold green]")
    console.print("[dim]Open your Telegram bot and send /start[/dim]\n")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .build()
    )

    app.add_handler(CommandHandler("start",      start_cmd))
    app.add_handler(CommandHandler("login",      login_cmd))
    app.add_handler(CommandHandler("setgroups",  setgroups_cmd))
    app.add_handler(CommandHandler("setmessage", setmessage_cmd))
    app.add_handler(CommandHandler("startbot",   startbot_cmd))
    app.add_handler(CommandHandler("stopbot",    stopbot_cmd))
    app.add_handler(CommandHandler("status",     status_cmd))
    app.add_handler(CommandHandler("logout",     logout_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    console.print("[bold cyan]🤖 Bot is live![/bold cyan]")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
