import os
import random
import string
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
import httpx

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8625002120:AAHJmcspMjsOW5IbrxprdQgX8gZ3Ss7wGFw")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "suteam19").lstrip("@")

_admin_ids_env = os.environ.get("ADMIN_IDS", "8673895274")
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_env.split(",") if x.strip().isdigit()]

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "jannat2764").lstrip("@")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "srrakib321").lstrip("@")
API_URL = os.environ.get("API_URL", "https://kalqqkfhzkj.vercel.app/bomb")

# ---- Database path (Render persistent disk mounts at /data) ----
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    DATA_DIR = "/data" if os.path.isdir("/data") else "."
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = "."
DB_FILE = os.path.join(DATA_DIR, "bot_database.db")

# Pricing
SMS_DURATIONS = {
    "2": {"minutes": 2, "credits": 5},
    "3": {"minutes": 3, "credits": 8},
    "4": {"minutes": 4, "credits": 10},
    "5": {"minutes": 5, "credits": 12},
}

CREDIT_PACKAGES = {
    "20_credits":  {"credits": 20,  "price": 20,  "label": "20 Credits — ৳20"},
    "50_credits":  {"credits": 50,  "price": 40,  "label": "50 Credits — ৳40"},
    "100_credits": {"credits": 100, "price": 70,  "label": "100 Credits — ৳70"},
    "vip_7":       {"credits": 0,   "price": 80,  "label": "VIP 7 Days — ৳80",  "vip_days": 7},
    "vip_30":      {"credits": 0,   "price": 200, "label": "VIP 30 Days — ৳200", "vip_days": 30},
}

DAILY_BONUS_CREDITS = 2
DAILY_BONUS_COOLDOWN_HOURS = 24
REFERRAL_REWARD_CREDITS = 5

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Menu texts (used to clear any pending state)
MENU_TEXTS = {
    "📩 SMS Bomber", "👤 Profile", "💳 Buy Subscription & Credit",
    "🎁 Daily Bonus", "🎁 Refer & Earn", "🎟 Redeem Code",
    "📞 Support", "⚙️ Admin Panel", "⬅️ Back",
}


# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        username TEXT,
        credits INTEGER DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        vip_expiry TEXT,
        is_banned INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referred_by INTEGER,
        daily_bonus_claimed TEXT,
        registered_at TEXT,
        total_requests INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS redeem_codes (
        code TEXT PRIMARY KEY,
        credits INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        expiry TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS redeemed_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        code TEXT,
        credits INTEGER,
        redeemed_at TEXT,
        UNIQUE(user_id, code)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        package TEXT,
        amount INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )""")
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_FILE}")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def get_user(user_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def create_user(user_id, name, username, referral_code=None, referred_by=None):
    conn = get_db()
    try:
        if not referral_code:
            referral_code = generate_referral_code()
        conn.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, name, username, referral_code, referred_by, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, username, referral_code, referred_by, datetime.now().isoformat()),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error creating user: {e}")
    finally:
        conn.close()


def update_user_credits(user_id, amount, operation="add"):
    conn = get_db()
    try:
        if operation == "add":
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
        elif operation == "subtract":
            conn.execute("UPDATE users SET credits = MAX(0, credits - ?) WHERE user_id = ?", (amount, user_id))
        elif operation == "set":
            conn.execute("UPDATE users SET credits = ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating credits: {e}")
        return False
    finally:
        conn.close()


def generate_referral_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_redeem_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=12))


def is_admin(user_id):
    return user_id in ADMIN_IDS


def is_vip_active(user_row):
    if not user_row:
        return False
    if not user_row["is_vip"]:
        return False
    if not user_row["vip_expiry"]:
        return False
    try:
        return datetime.fromisoformat(user_row["vip_expiry"]) > datetime.now()
    except Exception:
        return False


# ==================== KEYBOARDS ====================
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("📩 SMS Bomber"), KeyboardButton("👤 Profile")],
        [KeyboardButton("💳 Buy Subscription & Credit"), KeyboardButton("🎁 Daily Bonus")],
        [KeyboardButton("🎁 Refer & Earn"), KeyboardButton("🎟 Redeem Code")],
        [KeyboardButton("📞 Support")],
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_back_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("⬅️ Back")]], resize_keyboard=True)


def get_duration_keyboard():
    keyboard = [
        [InlineKeyboardButton("2 Minutes (5 credits)", callback_data="sms_dur_2"),
         InlineKeyboardButton("3 Minutes (8 credits)", callback_data="sms_dur_3")],
        [InlineKeyboardButton("4 Minutes (10 credits)", callback_data="sms_dur_4"),
         InlineKeyboardButton("5 Minutes (12 credits)", callback_data="sms_dur_5")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sms_cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Total Users", callback_data="admin_stats")],
        [InlineKeyboardButton("📜 User List", callback_data="admin_userlist_0")],
        [InlineKeyboardButton("➕ Add Credit", callback_data="admin_addcredit")],
        [InlineKeyboardButton("🧹 Reset Credit", callback_data="admin_resetcredit")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban")],
        [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban")],
        [InlineKeyboardButton("🎟 Gen Redeem Code", callback_data="admin_gencode")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⭐ VIP Management", callback_data="admin_vip")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="admin_mainmenu")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== HELPERS ====================
async def send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="Main Menu"):
    """Send a fresh message with the main reply keyboard (avoids edit-message issues)."""
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=get_main_keyboard(chat_id))


async def call_bomb_api(number, minutes):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(API_URL, json={"number": number, "amount": minutes})
            return r.status_code, r.text
    except Exception as e:
        return None, str(e)


# ==================== USER HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Ban check first
    existing = get_user(user_id)
    if existing and existing["is_banned"]:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # Channel membership
    try:
        chat_member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id
        )
        if chat_member.status in ("left", "kicked"):
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
                [InlineKeyboardButton("✅ Joined", callback_data="check_join")],
            ]
            await update.message.reply_text(
                f"⚠️ *You Must Join Our Channel First!*\n\n📢 Channel: @{CHANNEL_USERNAME}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
    except Exception as e:
        logger.warning(f"Channel check failed (bot must be admin in @{CHANNEL_USERNAME}): {e}")

    # Referral
    referred_by = None
    if context.args:
        ref_code = context.args[0].strip().upper()
        conn = get_db()
        try:
            referrer = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)
            ).fetchone()
        finally:
            conn.close()
        if referrer and referrer["user_id"] != user_id:
            referred_by = referrer["user_id"]

    if not existing:
        create_user(user_id, user.first_name, user.username, referred_by=referred_by)
        if referred_by:
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                    (REFERRAL_REWARD_CREDITS, referred_by),
                )
                conn.commit()
            finally:
                conn.close()
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 You earned {REFERRAL_REWARD_CREDITS} credits from a new referral!",
                )
            except Exception:
                pass
    else:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET name = ?, username = ? WHERE user_id = ?",
                (user.first_name, user.username, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    await update.message.reply_text(
        f"✅ Welcome {user.first_name}!\n\nUse the menu below to navigate.",
        reply_markup=get_main_keyboard(user_id),
    )


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    try:
        chat_member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id
        )
        joined = chat_member.status not in ("left", "kicked")
    except Exception as e:
        logger.warning(f"Channel check failed: {e}")
        joined = True  # fail-open if bot can't check

    if not joined:
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("✅ Joined", callback_data="check_join")],
        ]
        await query.edit_message_text(
            f"⚠️ *You Must Join Our Channel First!*\n\n📢 Channel: @{CHANNEL_USERNAME}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    await query.edit_message_text("✅ Verified!")
    # Send fresh message with main keyboard (ReplyKeyboardMarkup can't be attached to edit)
    await context.bot.send_message(
        chat_id=user_id, text="Main Menu", reply_markup=get_main_keyboard(user_id)
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    user_id = user.id
    text = update.message.text or ""

    # Ensure user exists
    existing = get_user(user_id)
    if not existing:
        create_user(user_id, user.first_name, user.username)
        existing = get_user(user_id)

    # Ban check
    if existing and existing["is_banned"]:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # ---- Menu buttons (clear pending states) ----
    if text in MENU_TEXTS:
        context.user_data["state"] = None
        context.user_data["admin_state"] = None

        if text == "📩 SMS Bomber":
            context.user_data["state"] = "awaiting_number"
            await update.message.reply_text(
                "📩 *SMS Bomber*\n\nSend me a phone number to start.\nExample: `018XXXXXXXX`",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard(),
            )
            return

        if text == "👤 Profile":
            user_data = get_user(user_id)
            vip_status = "VIP ⭐" if is_vip_active(user_data) else "Normal"
            vip_expiry = ""
            if user_data["is_vip"] and user_data["vip_expiry"]:
                vip_expiry = f"\nVIP Expiry: {user_data['vip_expiry'][:10]}"
            await update.message.reply_text(
                f"👤 *Profile*\n\n"
                f"Name: {user_data['name']}\n"
                f"User ID: `{user_data['user_id']}`\n"
                f"Credit: {user_data['credits']}\n"
                f"Member Type: {vip_status}{vip_expiry}",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(user_id),
            )
            return

        if text == "💳 Buy Subscription & Credit":
            msg = "💳 *Buy Subscription & Credit*\n\n"
            for _, pkg in CREDIT_PACKAGES.items():
                msg += f"• {pkg['label']}\n"
            msg += (
                "\n💰 *Payment Instructions:*\n"
                f"Contact admin @{ADMIN_USERNAME} to purchase.\n"
                "Send payment proof to admin.\n"
                "Credits/VIP will be added after verification."
            )
            keyboard = [[InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]]
            await update.message.reply_text(
                msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if text == "🎁 Daily Bonus":
            user_data = get_user(user_id)
            now = datetime.now()
            if user_data["daily_bonus_claimed"]:
                try:
                    last_claim = datetime.fromisoformat(user_data["daily_bonus_claimed"])
                    next_claim = last_claim + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)
                    if now < next_claim:
                        remaining = next_claim - now
                        hours = remaining.seconds // 3600
                        minutes = (remaining.seconds % 3600) // 60
                        await update.message.reply_text(
                            f"⏳ You already claimed today's bonus!\nNext claim in: {hours}h {minutes}m",
                            reply_markup=get_main_keyboard(user_id),
                        )
                        return
                except Exception:
                    pass
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE users SET credits = credits + ?, daily_bonus_claimed = ? WHERE user_id = ?",
                    (DAILY_BONUS_CREDITS, now.isoformat(), user_id),
                )
                conn.commit()
            finally:
                conn.close()
            new_bal = get_user(user_id)["credits"]
            await update.message.reply_text(
                f"🎁 Daily Bonus Claimed!\n\n+{DAILY_BONUS_CREDITS} credits added.\nNew balance: {new_bal} credits",
                reply_markup=get_main_keyboard(user_id),
            )
            return

        if text == "🎁 Refer & Earn":
            user_data = get_user(user_id)
            if not user_data["referral_code"]:
                code = generate_referral_code()
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE users SET referral_code = ? WHERE user_id = ?", (code, user_id)
                    )
                    conn.commit()
                finally:
                    conn.close()
                user_data = get_user(user_id)
            bot_username = context.bot.username
            ref_link = f"https://t.me/{bot_username}?start={user_data['referral_code']}"
            await update.message.reply_text(
                f"🎁 *Refer & Earn*\n\n"
                f"Share this link and earn {REFERRAL_REWARD_CREDITS} credits per referral!\n\n"
                f"🔗 Your link:\n`{ref_link}`\n\n"
                f"Reward: {REFERRAL_REWARD_CREDITS} credits per new user",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(user_id),
            )
            return

        if text == "🎟 Redeem Code":
            context.user_data["state"] = "awaiting_redeem_code"
            await update.message.reply_text(
                "🎟 *Redeem Code*\n\nSend me your redeem code:",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard(),
            )
            return

        if text == "📞 Support":
            msg = (
                "📞 *Support*\n\n"
                "💰 *How credits work:*\n"
                "Credits are used to run SMS tests. Each test costs credits based on duration.\n\n"
                "📩 *SMS Test Pricing:*\n"
                "• 2 minutes = 5 credits\n"
                "• 3 minutes = 8 credits\n"
                "• 4 minutes = 10 credits\n"
                "• 5 minutes = 12 credits\n\n"
                "⭐ *VIP:* Unlimited usage without credit deduction.\n\n"
                "💳 *How to buy credits/VIP:*\n"
                f"Contact @{ADMIN_USERNAME} for purchase.\n\n"
                "🎟 *How to use redeem codes:*\n"
                "Go to Redeem Code menu and enter your code.\n\n"
                f"👤 *Admin:* @{ADMIN_USERNAME}"
            )
            keyboard = [[InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]]
            await update.message.reply_text(
                msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if text == "⚙️ Admin Panel":
            if not is_admin(user_id):
                await update.message.reply_text("⛔ Unauthorized.")
                return
            await update.message.reply_text(
                "⚙️ *Admin Panel*\n\nSelect an option:",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard(),
            )
            return

        if text == "⬅️ Back":
            await update.message.reply_text("Main Menu", reply_markup=get_main_keyboard(user_id))
            return

    # ---- Admin state takes priority over normal states ----
    if is_admin(user_id) and context.user_data.get("admin_state"):
        handled = await handle_admin_input(update, context)
        if handled:
            return

    # ---- User states ----
    state = context.user_data.get("state")

    if state == "awaiting_number":
        number = text.strip().lstrip("+")
        if not number.isdigit() or len(number) < 10 or len(number) > 15:
            await update.message.reply_text("❌ Invalid phone number. Please send a valid number.")
            return
        context.user_data["sms_number"] = number
        context.user_data["state"] = "awaiting_duration"
        await update.message.reply_text(
            f"📱 Number: `{number}`\n\nSelect duration:",
            parse_mode="Markdown",
            reply_markup=get_duration_keyboard(),
        )
        return

    if state == "awaiting_redeem_code":
        code = text.strip().upper()
        conn = get_db()
        try:
            already = conn.execute(
                "SELECT * FROM redeemed_history WHERE user_id = ? AND code = ?",
                (user_id, code),
            ).fetchone()
            if already:
                await update.message.reply_text(
                    "❌ You already redeemed this code.", reply_markup=get_main_keyboard(user_id)
                )
                return

            code_data = conn.execute("SELECT * FROM redeem_codes WHERE code = ?", (code,)).fetchone()
            if not code_data:
                await update.message.reply_text(
                    "❌ Invalid code.", reply_markup=get_main_keyboard(user_id)
                )
                return
            if code_data["expiry"]:
                try:
                    if datetime.fromisoformat(code_data["expiry"]) < datetime.now():
                        await update.message.reply_text(
                            "❌ Code expired.", reply_markup=get_main_keyboard(user_id)
                        )
                        return
                except Exception:
                    pass
            if code_data["used_count"] >= code_data["max_uses"]:
                await update.message.reply_text(
                    "❌ Code usage limit reached.", reply_markup=get_main_keyboard(user_id)
                )
                return

            conn.execute("UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?", (code,))
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (code_data["credits"], user_id))
            conn.execute(
                "INSERT INTO redeemed_history (user_id, code, credits, redeemed_at) VALUES (?, ?, ?, ?)",
                (user_id, code, code_data["credits"], datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        context.user_data["state"] = None
        new_bal = get_user(user_id)["credits"]
        await update.message.reply_text(
            f"✅ Code redeemed!\n+{code_data['credits']} credits added.\nNew balance: {new_bal} credits",
            reply_markup=get_main_keyboard(user_id),
        )
        return

    # Default
    await update.message.reply_text("Use the menu below.", reply_markup=get_main_keyboard(user_id))


async def sms_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "sms_cancel":
        context.user_data["state"] = None
        await query.edit_message_text("❌ Cancelled.")
        await context.bot.send_message(
            chat_id=user_id, text="Main Menu", reply_markup=get_main_keyboard(user_id)
        )
        return

    duration_key = data.replace("sms_dur_", "")
    if duration_key not in SMS_DURATIONS:
        await query.edit_message_text("❌ Invalid duration.")
        return

    duration = SMS_DURATIONS[duration_key]
    number = context.user_data.get("sms_number")
    if not number:
        await query.edit_message_text("❌ Session expired. Please start again.")
        return

    user_data = get_user(user_id)
    vip = is_vip_active(user_data)

    # Deduct credits (unless VIP)
    if not vip:
        if user_data["credits"] < duration["credits"]:
            await query.edit_message_text(
                f"❌ *Insufficient Credits!*\n\n"
                f"Required: {duration['credits']} credits\n"
                f"Your balance: {user_data['credits']} credits\n\n"
                f"Please buy credits from the menu.",
                parse_mode="Markdown",
            )
            context.user_data["state"] = None
            return
        update_user_credits(user_id, duration["credits"], "subtract")

    # Call API
    status, body = await call_bomb_api(number, duration["minutes"])
    ok = status is not None and 200 <= status < 300

    if not ok:
        # Refund if deduction happened
        if not vip:
            update_user_credits(user_id, duration["credits"], "add")
        logger.error(f"API Error status={status} body={body}")
        await query.edit_message_text(
            "❌ *Request failed.*\nYour credits have been refunded. Please try again later.",
            parse_mode="Markdown",
        )
        context.user_data["state"] = None
        await context.bot.send_message(
            chat_id=user_id, text="Main Menu", reply_markup=get_main_keyboard(user_id)
        )
        return

    # Update request count
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET total_requests = total_requests + 1 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

    await query.edit_message_text(
        f"✅ *Request Sent!*\n\n"
        f"📱 Number: `{number}`\n"
        f"⏱ Duration: {duration['minutes']} minutes\n"
        f"💳 Credits used: {0 if vip else duration['credits']}{' (VIP)' if vip else ''}\n\n"
        f"Your request is being processed...",
        parse_mode="Markdown",
    )

    # Notify admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_IDS[0],
            text=(
                f"🚨 NEW REQUEST\n\n"
                f"👤 {user_data['name']} (@{user_data['username'] or 'NoUsername'})\n"
                f"🆔 `{user_id}`\n"
                f"📱 {number}\n"
                f"⏱ {duration['minutes']} minutes\n"
                f"💳 {duration['credits']} credits"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Admin notify error: {e}")

    context.user_data["state"] = None
    await context.bot.send_message(
        chat_id=user_id, text="Main Menu", reply_markup=get_main_keyboard(user_id)
    )


# ==================== ADMIN HANDLERS ====================
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.edit_message_text("⛔ Unauthorized.")
        return

    data = query.data

    if data == "admin_mainmenu":
        await query.edit_message_text("Returning to main menu...")
        await context.bot.send_message(
            chat_id=user_id, text="Main Menu", reply_markup=get_main_keyboard(user_id)
        )
        return

    if data == "admin_stats":
        conn = get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            vip = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1").fetchone()[0]
            banned = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0]
            total_requests = conn.execute("SELECT SUM(total_requests) FROM users").fetchone()[0] or 0
        finally:
            conn.close()
        await query.edit_message_text(
            f"📊 *Statistics*\n\n"
            f"👥 Total Users: {total}\n"
            f"⭐ VIP Users: {vip}\n"
            f"🚫 Banned Users: {banned}\n"
            f"📩 Total Requests: {total_requests}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(),
        )
        return

    if data.startswith("admin_userlist_"):
        page = int(data.split("_")[2])
        conn = get_db()
        try:
            users = conn.execute(
                "SELECT * FROM users ORDER BY user_id LIMIT 10 OFFSET ?", (page * 10,)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()

        total_pages = max(1, (total + 9) // 10)
        msg = f"📜 *User List* (Page {page + 1}/{total_pages})\n\n"
        for u in users:
            vip = "⭐" if u["is_vip"] else "👤"
            ban = " 🚫" if u["is_banned"] else ""
            msg += f"{vip} `{u['user_id']}` — {u['name']} — {u['credits']} cr{ban}\n"

        keyboard = []
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_userlist_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_userlist_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_addcredit":
        context.user_data["admin_state"] = "addcredit_id"
        await query.edit_message_text("➕ *Add Credit*\n\nEnter the Telegram User ID:", parse_mode="Markdown")
        return

    if data == "admin_resetcredit":
        context.user_data["admin_state"] = "resetcredit_id"
        await query.edit_message_text("🧹 *Reset Credit*\n\nEnter the Telegram User ID:", parse_mode="Markdown")
        return

    if data == "admin_ban":
        context.user_data["admin_state"] = "ban_id"
        await query.edit_message_text("🚫 *Ban User*\n\nEnter the Telegram User ID:", parse_mode="Markdown")
        return

    if data == "admin_unban":
        context.user_data["admin_state"] = "unban_id"
        await query.edit_message_text("✅ *Unban User*\n\nEnter the Telegram User ID:", parse_mode="Markdown")
        return

    if data == "admin_gencode":
        context.user_data["admin_state"] = "gencode_credits"
        await query.edit_message_text("🎟 *Generate Redeem Code*\n\nEnter credit amount:", parse_mode="Markdown")
        return

    if data == "admin_broadcast":
        context.user_data["admin_state"] = "broadcast_msg"
        await query.edit_message_text("📢 *Broadcast*\n\nSend the message to broadcast:", parse_mode="Markdown")
        return

    if data == "admin_vip":
        context.user_data["admin_state"] = "vip_id"
        await query.edit_message_text("⭐ *VIP Management*\n\nEnter the Telegram User ID:", parse_mode="Markdown")
        return

    if data == "admin_back":
        await query.edit_message_text("⚙️ Admin Panel", reply_markup=get_admin_keyboard())
        return


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if handled."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return False

    text = (update.message.text or "").strip()
    state = context.user_data.get("admin_state")
    if not state:
        return False

    # ---------- Add credit ----------
    if state == "addcredit_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid User ID.")
            return True
        target_id = int(text)
        target = get_user(target_id)
        if not target:
            await update.message.reply_text("❌ User not found.")
            return True
        context.user_data["admin_target"] = target_id
        context.user_data["admin_state"] = "addcredit_amount"
        await update.message.reply_text(
            f"👤 User: {target['name']}\n💰 Current Credits: {target['credits']}\n\n"
            f"Enter amount to add (use -amount to subtract):"
        )
        return True

    if state == "addcredit_amount":
        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return True
        target_id = context.user_data.get("admin_target")
        target = get_user(target_id)
        if not target:
            await update.message.reply_text("❌ User not found.")
            context.user_data["admin_state"] = None
            return True
        conn = get_db()
        try:
            if amount >= 0:
                conn.execute(
                    "UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, target_id)
                )
            else:
                conn.execute(
                    "UPDATE users SET credits = MAX(0, credits + ?) WHERE user_id = ?", (amount, target_id)
                )
            conn.commit()
        finally:
            conn.close()
        updated = get_user(target_id)
        context.user_data["admin_state"] = None
        await update.message.reply_text(
            f"✅ Credits updated!\nUser: `{target_id}`\nAdded: {amount:+d}\nNew balance: {updated['credits']}",
            parse_mode="Markdown",
        )
        return True

    # ---------- Reset credit ----------
    if state == "resetcredit_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid User ID.")
            return True
        target_id = int(text)
        if not get_user(target_id):
            await update.message.reply_text("❌ User not found.")
            return True
        conn = get_db()
        try:
            conn.execute("UPDATE users SET credits = 0 WHERE user_id = ?", (target_id,))
            conn.commit()
        finally:
            conn.close()
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Reset credits for `{target_id}` to 0.", parse_mode="Markdown")
        return True

    # ---------- Ban ----------
    if state == "ban_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid User ID.")
            return True
        target_id = int(text)
        if not get_user(target_id):
            await update.message.reply_text("❌ User not found.")
            return True
        conn = get_db()
        try:
            conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
            conn.commit()
        finally:
            conn.close()
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Banned `{target_id}`.", parse_mode="Markdown")
        return True

    # ---------- Unban ----------
    if state == "unban_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid User ID.")
            return True
        target_id = int(text)
        if not get_user(target_id):
            await update.message.reply_text("❌ User not found.")
            return True
        conn = get_db()
        try:
            conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
            conn.commit()
        finally:
            conn.close()
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Unbanned `{target_id}`.", parse_mode="Markdown")
        return True

    # ---------- Gen code (credits) ----------
    if state == "gencode_credits":
        try:
            credits = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return True
        context.user_data["gencode_credits"] = credits
        context.user_data["admin_state"] = "gencode_maxuses"
        await update.message.reply_text("Enter max uses:")
        return True

    if state == "gencode_maxuses":
        try:
            max_uses = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid number.")
            return True
        credits = context.user_data.get("gencode_credits", 0)
        code = generate_redeem_code()
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO redeem_codes (code, credits, max_uses, created_at) VALUES (?, ?, ?, ?)",
                (code, credits, max_uses, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        context.user_data["admin_state"] = None
        await update.message.reply_text(
            f"🎟 *Redeem Code Generated!*\n\nCode: `{code}`\nCredits: {credits}\nMax Uses: {max_uses}",
            parse_mode="Markdown",
        )
        return True

    # ---------- Broadcast ----------
    if state == "broadcast_msg":
        conn = get_db()
        try:
            all_users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        finally:
            conn.close()

        context.user_data["admin_state"] = None
        success = 0
        failed = 0
        for u in all_users:
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=text)
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # rate limit friendly
        await update.message.reply_text(
            f"📢 *Broadcast Complete!*\n\n✅ Sent: {success}\n❌ Failed: {failed}",
            parse_mode="Markdown",
        )
        return True

    # ---------- VIP ----------
    if state == "vip_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid User ID.")
            return True
        target_id = int(text)
        target = get_user(target_id)
        if not target:
            await update.message.reply_text("❌ User not found.")
            return True
        context.user_data["admin_target"] = target_id
        context.user_data["admin_state"] = "vip_days"
        await update.message.reply_text(
            f"👤 User: {target['name']}\n"
            f"⭐ Current VIP: {'Yes' if target['is_vip'] else 'No'}\n\n"
            f"Enter VIP days (0 to remove VIP):"
        )
        return True

    if state == "vip_days":
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("❌ Invalid number.")
            return True
        target_id = context.user_data.get("admin_target")
        conn = get_db()
        try:
            if days <= 0:
                conn.execute(
                    "UPDATE users SET is_vip = 0, vip_expiry = NULL WHERE user_id = ?", (target_id,)
                )
            else:
                expiry = (datetime.now() + timedelta(days=days)).isoformat()
                conn.execute(
                    "UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?",
                    (expiry, target_id),
                )
            conn.commit()
        finally:
            conn.close()
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ VIP updated for `{target_id}`.", parse_mode="Markdown")
        return True

    return False


# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


# ==================== MAIN ====================
def build_application():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(sms_duration_callback, pattern="^sms_"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.add_error_handler(error_handler)
    return application


def main():
    init_db()
    application = build_application()

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    webhook_base = os.environ.get("WEBHOOK_URL", render_url).strip()
    port = int(os.environ.get("PORT", "8443"))

    if webhook_base.startswith("http"):
        url_path = BOT_TOKEN
        full_webhook = f"{webhook_base.rstrip('/')}/{url_path}"
        logger.info(f"Starting webhook mode on 0.0.0.0:{port} -> {full_webhook}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=url_path,
            webhook_url=full_webhook,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Starting polling mode (no WEBHOOK_URL / RENDER_EXTERNAL_URL set)")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )


if __name__ == "__main__":
    main()