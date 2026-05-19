"""
╔══════════════════════════════════════════════════════════╗
║         TELEGRAM AUTO MESSAGE SENDER                    ║
║         Bot-Based Login | Multi-Group | Cloud Ready     ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import random
import json
import logging
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PeerFloodError, UserBannedInChannelError,
    ChatWriteForbiddenError, SessionPasswordNeededError
)
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich import box
import sys

# ─────────────────────────────────────────────
#              CONFIGURATION
# ─────────────────────────────────────────────

API_ID       = int(os.environ.get("API_ID", "YOUR_API_ID"))
API_HASH     = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8821493954:AAFq_3lBHYUahOanJgIVcV3HielzuPPLRTk")
OWNER_ID     = int(os.environ.get("OWNER_ID", "YOUR_TELEGRAM_USER_ID"))  # Your Telegram numeric ID
SESSION_FILE = "session_data.json"

# Delay between each group message (seconds) - random range to avoid detection
MIN_DELAY = 45   # minimum seconds between groups
MAX_DELAY = 120  # maximum seconds between groups

# Delay between full cycles (seconds)
CYCLE_DELAY_MIN = 3600  # 1 hour
CYCLE_DELAY_MAX = 7200  # 2 hours

# ─────────────────────────────────────────────
#              SETUP
# ─────────────────────────────────────────────

console = Console()
logging.basicConfig(level=logging.WARNING)

# State management
state = {
    "session_string": None,
    "phone": None,
    "groups": [],
    "message": "",
    "running": False,
    "logged_in": False,
    "awaiting": None,  # 'phone', 'otp', 'password', 'groups', 'message'
    "phone_code_hash": None,
    "user_client": None,
    "stats": {"sent": 0, "failed": 0, "cycles": 0}
}

# ─────────────────────────────────────────────
#           SESSION PERSISTENCE
# ─────────────────────────────────────────────

def save_session(session_string, phone):
    data = {"session": session_string, "phone": phone}
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f)

def load_session():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            data = json.load(f)
            return data.get("session"), data.get("phone")
    return None, None

# ─────────────────────────────────────────────
#           TELEGRAM BOT HANDLERS
# ─────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return

    menu = (
        "╔══════════════════════════════╗\n"
        "║   📡 AUTO SENDER BOT PANEL  ║\n"
        "╚══════════════════════════════╝\n\n"
        "📋 *Available Commands:*\n\n"
        "🔐 `/login` — Start Telegram login\n"
        "👥 `/setgroups` — Set target groups\n"
        "✉️ `/setmessage` — Set custom message\n"
        "▶️ `/startbot` — Start auto sending\n"
        "⏹ `/stopbot` — Stop sending\n"
        "📊 `/status` — View current status\n"
        "🔄 `/logout` — Logout session\n"
    )
    await update.message.reply_text(menu, parse_mode="Markdown")


async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    # Check if already logged in
    session_str, phone = load_session()
    if session_str:
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                state["user_client"] = client
                state["logged_in"] = True
                state["session_string"] = session_str
                me = await client.get_me()
                await update.message.reply_text(
                    f"✅ Already logged in as *{me.first_name}* (`{me.phone}`)\n\n"
                    f"Use /setgroups to set target groups.",
                    parse_mode="Markdown"
                )
                return
        except Exception:
            pass

    state["awaiting"] = "phone"
    await update.message.reply_text(
        "📱 *Login to Telegram*\n\n"
        "Please send your phone number with country code:\n"
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
        "Send group usernames or links, one per line.\n\n"
        "Example:\n"
        "`@mygroup1\n"
        "@mygroup2\n"
        "https://t.me/mygroup3`",
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
        "✉️ *Set Custom Message*\n\n"
        "Send the message you want to broadcast to all groups.\n"
        "You can use emojis and formatting.",
        parse_mode="Markdown"
    )


async def startbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not state["logged_in"]:
        await update.message.reply_text("❌ Please /login first.")
        return
    if not state["groups"]:
        await update.message.reply_text("❌ No groups set. Use /setgroups first.")
        return
    if not state["message"]:
        await update.message.reply_text("❌ No message set. Use /setmessage first.")
        return
    if state["running"]:
        await update.message.reply_text("⚠️ Already running!")
        return

    state["running"] = True
    await update.message.reply_text(
        f"▶️ *Auto Sender Started!*\n\n"
        f"👥 Groups: `{len(state['groups'])}`\n"
        f"⏱ Delay: `{MIN_DELAY}–{MAX_DELAY}s` between groups\n"
        f"🔄 Cycle Delay: `{CYCLE_DELAY_MIN//60}–{CYCLE_DELAY_MAX//60} mins`\n\n"
        f"Use /stopbot to stop.",
        parse_mode="Markdown"
    )

    # Start the sending loop in background
    asyncio.create_task(sending_loop(context.application.bot))


async def stopbot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    state["running"] = False
    await update.message.reply_text("⏹ *Auto Sender Stopped.*", parse_mode="Markdown")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    status_icon = "🟢 Running" if state["running"] else "🔴 Stopped"
    login_icon  = "✅ Logged In" if state["logged_in"] else "❌ Not Logged In"

    msg_preview = (state["message"][:80] + "...") if len(state["message"]) > 80 else state["message"]

    text = (
        f"📊 *STATUS REPORT*\n"
        f"{'─'*30}\n"
        f"🔐 Login: {login_icon}\n"
        f"⚙️ Sender: {status_icon}\n"
        f"👥 Groups: `{len(state['groups'])}`\n"
        f"📤 Sent: `{state['stats']['sent']}`\n"
        f"❌ Failed: `{state['stats']['failed']}`\n"
        f"🔄 Cycles: `{state['stats']['cycles']}`\n\n"
        f"📝 *Message Preview:*\n`{msg_preview}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    state["running"] = False
    state["logged_in"] = False
    state["session_string"] = None
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    if state["user_client"]:
        await state["user_client"].disconnect()
        state["user_client"] = None
    await update.message.reply_text("🔄 Logged out successfully. Use /login to login again.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = update.message.text.strip()

    # ── PHONE NUMBER ──
    if state["awaiting"] == "phone":
        state["phone"] = text
        state["awaiting"] = None

        await update.message.reply_text("⏳ Sending OTP to your Telegram...")

        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(text)
            state["phone_code_hash"] = result.phone_code_hash
            state["user_client"] = client
            state["awaiting"] = "otp"

            await update.message.reply_text(
                "📲 *OTP Sent!*\n\n"
                "Please send the OTP you received on Telegram.\n"
                "Format: `12345`",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: `{e}`\n\nTry /login again.", parse_mode="Markdown")

    # ── OTP ──
    elif state["awaiting"] == "otp":
        otp = text.replace(" ", "")
        state["awaiting"] = None
        client = state["user_client"]

        try:
            await client.sign_in(
                phone=state["phone"],
                code=otp,
                phone_code_hash=state["phone_code_hash"]
            )
            session_str = client.session.save()
            save_session(session_str, state["phone"])
            state["session_string"] = session_str
            state["logged_in"] = True

            me = await client.get_me()
            await update.message.reply_text(
                f"✅ *Login Successful!*\n\n"
                f"👤 Name: *{me.first_name}*\n"
                f"📱 Phone: `{me.phone}`\n\n"
                f"Now use /setgroups to set target groups.",
                parse_mode="Markdown"
            )
        except SessionPasswordNeededError:
            state["awaiting"] = "password"
            await update.message.reply_text(
                "🔒 *2FA Enabled*\n\n"
                "Please send your Two-Factor Authentication password:",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ OTP Error: `{e}`\n\nTry /login again.", parse_mode="Markdown")

    # ── 2FA PASSWORD ──
    elif state["awaiting"] == "password":
        state["awaiting"] = None
        client = state["user_client"]

        try:
            await client.sign_in(password=text)
            session_str = client.session.save()
            save_session(session_str, state["phone"])
            state["session_string"] = session_str
            state["logged_in"] = True

            me = await client.get_me()
            await update.message.reply_text(
                f"✅ *Login Successful (2FA)!*\n\n"
                f"👤 Name: *{me.first_name}*\n\n"
                f"Now use /setgroups to set target groups.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ 2FA Error: `{e}`\n\nTry /login again.", parse_mode="Markdown")

    # ── GROUPS ──
    elif state["awaiting"] == "groups":
        state["awaiting"] = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        state["groups"] = lines

        group_list = "\n".join([f"  `{g}`" for g in lines])
        await update.message.reply_text(
            f"✅ *{len(lines)} Group(s) Set:*\n\n{group_list}\n\n"
            f"Now use /setmessage to set your message.",
            parse_mode="Markdown"
        )

    # ── MESSAGE ──
    elif state["awaiting"] == "message":
        state["awaiting"] = None
        state["message"] = text
        preview = text[:100] + "..." if len(text) > 100 else text
        await update.message.reply_text(
            f"✅ *Message Set!*\n\n"
            f"Preview:\n`{preview}`\n\n"
            f"Use /startbot to begin sending.",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
#           AUTO SENDING LOOP
# ─────────────────────────────────────────────

async def sending_loop(bot: Bot):
    while state["running"]:
        state["stats"]["cycles"] += 1
        cycle_num = state["stats"]["cycles"]

        await bot.send_message(
            OWNER_ID,
            f"🔄 *Cycle #{cycle_num} Started*\n"
            f"📤 Sending to {len(state['groups'])} group(s)...",
            parse_mode="Markdown"
        )

        client = state["user_client"]

        for i, group in enumerate(state["groups"]):
            if not state["running"]:
                break

            try:
                entity = await client.get_entity(group)
                await client.send_message(entity, state["message"])
                state["stats"]["sent"] += 1

                await bot.send_message(
                    OWNER_ID,
                    f"✅ `[{i+1}/{len(state['groups'])}]` Sent to `{group}`",
                    parse_mode="Markdown"
                )

            except FloodWaitError as e:
                wait = e.seconds + random.randint(10, 30)
                await bot.send_message(
                    OWNER_ID,
                    f"⚠️ FloodWait! Waiting `{wait}s`...",
                    parse_mode="Markdown"
                )
                await asyncio.sleep(wait)

            except (PeerFloodError, UserBannedInChannelError, ChatWriteForbiddenError) as e:
                state["stats"]["failed"] += 1
                await bot.send_message(
                    OWNER_ID,
                    f"❌ Failed `{group}`: `{type(e).__name__}`",
                    parse_mode="Markdown"
                )

            except Exception as e:
                state["stats"]["failed"] += 1
                await bot.send_message(
                    OWNER_ID,
                    f"❌ Error on `{group}`: `{str(e)[:100]}`",
                    parse_mode="Markdown"
                )

            # Random delay between groups
            if i < len(state["groups"]) - 1 and state["running"]:
                delay = random.randint(MIN_DELAY, MAX_DELAY)
                await bot.send_message(
                    OWNER_ID,
                    f"⏳ Next group in `{delay}s`...",
                    parse_mode="Markdown"
                )
                await asyncio.sleep(delay)

        if not state["running"]:
            break

        # Cycle complete
        cycle_delay = random.randint(CYCLE_DELAY_MIN, CYCLE_DELAY_MAX)
        await bot.send_message(
            OWNER_ID,
            f"✅ *Cycle #{cycle_num} Complete!*\n\n"
            f"📤 Total Sent: `{state['stats']['sent']}`\n"
            f"❌ Failed: `{state['stats']['failed']}`\n"
            f"⏰ Next cycle in `{cycle_delay//60}` minutes...",
            parse_mode="Markdown"
        )
        await asyncio.sleep(cycle_delay)

# ─────────────────────────────────────────────
#                   MAIN
# ─────────────────────────────────────────────

def print_banner():
    console.print(Panel.fit(
        "[bold cyan]📡 TELEGRAM AUTO SENDER[/bold cyan]\n"
        "[dim]Bot-Based Login | Cloud Ready | Multi-Group[/dim]",
        border_style="cyan",
        padding=(1, 4)
    ))
    console.print()

    table = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False)
    table.add_column("Key", style="bold yellow")
    table.add_column("Value", style="white")
    table.add_row("Bot Token", BOT_TOKEN[:20] + "...")
    table.add_row("Owner ID", str(OWNER_ID))
    table.add_row("Min Delay", f"{MIN_DELAY}s")
    table.add_row("Max Delay", f"{MAX_DELAY}s")
    table.add_row("Cycle", f"{CYCLE_DELAY_MIN//60}–{CYCLE_DELAY_MAX//60} min")
    console.print(table)
    console.print()


def main():
    print_banner()

    console.print("[bold green]✅ Starting Bot...[/bold green]")
    console.print("[dim]Open your Telegram bot and send /start[/dim]\n")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("login", login_cmd))
    app.add_handler(CommandHandler("setgroups", setgroups_cmd))
    app.add_handler(CommandHandler("setmessage", setmessage_cmd))
    app.add_handler(CommandHandler("startbot", startbot_cmd))
    app.add_handler(CommandHandler("stopbot", stopbot_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    console.print("[bold cyan]🤖 Bot is polling...[/bold cyan]")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
