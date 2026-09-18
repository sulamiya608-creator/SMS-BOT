import os
import random
import string
import sqlite3
import requests
import logging
import asyncio
import threading
from urllib.parse import quote
from datetime import datetime, timedelta
from flask import Flask

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8625002120:AAHJmcspMjsOW5IbrxprdQgX8gZ3Ss7wGFw"

CHANNEL_USERNAME = "suteam19"          # without @
ADMIN_IDS = [8673895274]               # Admin Telegram IDs
ADMIN_USERNAME = "jannat2764"          # without @
DEVELOPER_USERNAME = "jannat2764"      # without @

API_URL = "https://kalqqkfhzkj.vercel.app/bomb"
CUSTOM_SMS_API = "https://stapi.st-shop.xyz/customsms.php"

SMS_DURATIONS = {
    "2": {"minutes": 2, "credits": 5},
    "3": {"minutes": 3, "credits": 8},
    "4": {"minutes": 4, "credits": 10},
    "5": {"minutes": 5, "credits": 12}
}

CREDIT_PACKAGES = {
    "20_credits": {"credits": 20, "price": 20, "label": "20 Credits — ৳20"},
    "50_credits": {"credits": 50, "price": 40, "label": "50 Credits — ৳40"},
    "100_credits": {"credits": 100, "price": 70, "label": "100 Credits — ৳70"},
    "vip_7": {"credits": 0, "price": 80, "label": "VIP 7 Days — ৳80", "vip_days": 7},
    "vip_30": {"credits": 0, "price": 200, "label": "VIP 30 Days — ৳200", "vip_days": 30}
}

DAILY_BONUS_CREDITS = 2
DAILY_BONUS_COOLDOWN_HOURS = 24
REFERRAL_REWARD_CREDITS = 5

# Custom SMS settings
CUSTOM_SMS_COST = 2
CUSTOM_SMS_MAX_LENGTH = 130

# Render-এ পারমিশন সমস্যা এড়াতে /tmp ব্যবহার
DB_FILE = "/tmp/bot_database.db"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK DUMMY SERVER ====================
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
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
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS redeem_codes (
        code TEXT PRIMARY KEY,
        credits INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        expiry TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS redeemed_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        code TEXT,
        credits INTEGER,
        redeemed_at TEXT,
        UNIQUE(user_id, code)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        package TEXT,
        amount INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return user

def create_user(user_id, name, username, referral_code=None, referred_by=None):
    conn = get_db()
    try:
        if not referral_code:
            referral_code = generate_referral_code()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, name, username, referral_code, referred_by, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, username, referral_code, referred_by, datetime.now().isoformat())
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error creating user: {e}")
    finally:
        conn.close()

def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_redeem_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_vip_active(user):
    if not user or not user['is_vip'] or not user['vip_expiry']:
        return False
    try:
        return datetime.fromisoformat(user['vip_expiry']) > datetime.now()
    except:
        return False

def is_valid_bd_number(number):
    """Bangladesh mobile number validation: 11 digits starting with 01"""
    if not number.isdigit() or len(number) != 11:
        return False
    if not number.startswith("01"):
        return False
    # Valid operator prefixes: 013-019
    if number[2] not in "3456789":
        return False
    return True

# ==================== KEYBOARDS ====================
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("📩 SMS Bomber"), KeyboardButton("✉️ Custom SMS")],
        [KeyboardButton("👤 Profile"), KeyboardButton("💳 Buy Subscription & Credit")],
        [KeyboardButton("🎁 Daily Bonus"), KeyboardButton("🎁 Refer & Earn")],
        [KeyboardButton("🎟 Redeem Code"), KeyboardButton("📞 Support")]
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("⬅️ Back")]], resize_keyboard=True)

def get_duration_keyboard():
    keyboard = [
        [InlineKeyboardButton("2 Min (5 cr)", callback_data="sms_dur_2"),
         InlineKeyboardButton("3 Min (8 cr)", callback_data="sms_dur_3")],
        [InlineKeyboardButton("4 Min (10 cr)", callback_data="sms_dur_4"),
         InlineKeyboardButton("5 Min (12 cr)", callback_data="sms_dur_5")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sms_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_custom_confirm_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Send SMS", callback_data="custom_send"),
         InlineKeyboardButton("❌ Cancel", callback_data="custom_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Total Users", callback_data="admin_stats")],
        [InlineKeyboardButton("📜 User List", callback_data="admin_userlist_0")],
        [InlineKeyboardButton("➕ Add/Sub Credit", callback_data="admin_addcredit")],
        [InlineKeyboardButton("🧹 Reset Credit", callback_data="admin_resetcredit")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban")],
        [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban")],
        [InlineKeyboardButton("🎟 Gen Redeem Code", callback_data="admin_gencode")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⭐ VIP Management", callback_data="admin_vip")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="admin_mainmenu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_join_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ Verify Join", callback_data="check_join")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== FORCE JOIN CHECK ====================
async def is_user_joined(context, user_id):
    try:
        member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}",
            user_id=user_id
        )
        if member.status in ["creator", "administrator", "member"]:
            return True
        if member.status == "restricted" and getattr(member, "is_member", False):
            return True
        return False
    except Exception as e:
        logger.error(f"ForceJoin check error: {e}")
        return True

async def send_join_prompt(update_or_query, context):
    text = (
        "⚠️ *You Must Join Our Channel First!*\n\n"
        f"📢 Channel: @{CHANNEL_USERNAME}\n\n"
        "1️⃣ Join the channel\n"
        "2️⃣ Then press ✅ Verify Join"
    )
    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=get_join_keyboard()
        )
    else:
        try:
            await update_or_query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=get_join_keyboard()
            )
        except Exception:
            await update_or_query.message.reply_text(
                text, parse_mode="Markdown", reply_markup=get_join_keyboard()
            )

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    joined = await is_user_joined(context, user_id)
    if not joined:
        await send_join_prompt(update, context)
        return

    referred_by = None
    if context.args:
        ref_code = context.args[0]
        conn = get_db()
        referrer = conn.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)).fetchone()
        conn.close()
        if referrer and referrer['user_id'] != user_id:
            referred_by = referrer['user_id']

    existing = get_user(user_id)
    if not existing:
        create_user(user_id, user.first_name, user.username, referred_by=referred_by)
        if referred_by:
            conn = get_db()
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?",
                         (REFERRAL_REWARD_CREDITS, referred_by))
            conn.commit()
            conn.close()
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 You earned {REFERRAL_REWARD_CREDITS} credits from a new referral!"
                )
            except:
                pass
    else:
        conn = get_db()
        conn.execute("UPDATE users SET name = ?, username = ? WHERE user_id = ?",
                     (user.first_name, user.username, user_id))
        conn.commit()
        conn.close()

    await update.message.reply_text(
        f"✅ Welcome {user.first_name}!\n\nUse the menu below to navigate.",
        reply_markup=get_main_keyboard(user_id)
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    joined = await is_user_joined(context, user_id)
    if not joined:
        await query.answer("❌ You haven't joined yet! Join the channel first.", show_alert=True)
        return

    await query.edit_message_text("✅ Verified! Use the menu below.")
    await context.bot.send_message(
        chat_id=user_id,
        text="Main Menu",
        reply_markup=get_main_keyboard(user_id)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text

    # Admin input priority
    if is_admin(user_id) and context.user_data.get('admin_state'):
        await handle_admin_input(update, context)
        return

    existing = get_user(user_id)
    if not existing:
        joined = await is_user_joined(context, user_id)
        if not joined:
            await send_join_prompt(update, context)
            return
        create_user(user_id, user.first_name, user.username)

    user_data = get_user(user_id)
    if user_data and user_data['is_banned']:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # ============ MAIN MENU ============
    if text == "📩 SMS Bomber":
        joined = await is_user_joined(context, user_id)
        if not joined:
            await send_join_prompt(update, context)
            return
        context.user_data['state'] = 'awaiting_number'
        await update.message.reply_text(
            "📩 *SMS Bomber*\n\nSend me a phone number (e.g. `018XXXXXXXX`):",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        return

    # ============ CUSTOM SMS ============
    elif text == "✉️ Custom SMS":
        joined = await is_user_joined(context, user_id)
        if not joined:
            await send_join_prompt(update, context)
            return
        context.user_data['state'] = 'custom_sms_number'
        context.user_data['custom_sms_number'] = None
        context.user_data['custom_sms_message'] = None
        await update.message.reply_text(
            f"✉️ *CUSTOM SMS*\n\n"
            f"Send 1 custom SMS\n"
            f"💳 Cost: {CUSTOM_SMS_COST} Credits per message\n\n"
            f"📱 Enter the recipient number:\n"
            f"Example: `01XXXXXXXXX`",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        return

    elif text == "👤 Profile":
        u = get_user(user_id)
        vip_type = "⭐ VIP" if is_vip_active(u) else "👤 Normal"
        vip_exp = f"\nVIP Expiry: {u['vip_expiry'][:10]}" if is_vip_active(u) else ""
        await update.message.reply_text(
            f"👤 *Profile*\n\n"
            f"Name: {u['name']}\n"
            f"User ID: `{u['user_id']}`\n"
            f"Credit: {u['credits']}\n"
            f"Member Type: {vip_type}{vip_exp}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    elif text == "💳 Buy Subscription & Credit":
        msg = "💳 *Buy Subscription & Credit*\n\n"
        for k, pkg in CREDIT_PACKAGES.items():
            msg += f"• {pkg['label']}\n"
        msg += f"\n💰 *Payment:* Contact @{ADMIN_USERNAME} and send payment proof."
        msg += "\nCredits/VIP added after admin verification."
        keyboard = [[InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]]
        await update.message.reply_text(msg, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif text == "🎁 Daily Bonus":
        u = get_user(user_id)
        now = datetime.now()
        if u['daily_bonus_claimed']:
            last = datetime.fromisoformat(u['daily_bonus_claimed'])
            nxt = last + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)
            if now < nxt:
                rem = nxt - now
                h, m = rem.seconds // 3600, (rem.seconds % 3600) // 60
                await update.message.reply_text(
                    f"⏳ Already claimed!\nNext in: {h}h {m}m",
                    reply_markup=get_main_keyboard(user_id)
                )
                return
        conn = get_db()
        conn.execute("UPDATE users SET credits = credits + ?, daily_bonus_claimed = ? WHERE user_id = ?",
                     (DAILY_BONUS_CREDITS, now.isoformat(), user_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"🎁 Daily Bonus Claimed!\n+{DAILY_BONUS_CREDITS} credits\nBalance: {get_user(user_id)['credits']}",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    elif text == "🎁 Refer & Earn":
        u = get_user(user_id)
        if not u['referral_code']:
            code = generate_referral_code()
            conn = get_db()
            conn.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (code, user_id))
            conn.commit()
            conn.close()
            u = get_user(user_id)
        link = f"https://t.me/{context.bot.username}?start={u['referral_code']}"
        await update.message.reply_text(
            f"🎁 *Refer & Earn*\n\n"
            f"🔗 Your link:\n`{link}`\n\n"
            f"💰 Reward: {REFERRAL_REWARD_CREDITS} credits per new user",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    elif text == "🎟 Redeem Code":
        context.user_data['state'] = 'awaiting_redeem_code'
        await update.message.reply_text(
            "🎟 Send your redeem code:",
            reply_markup=get_back_keyboard()
        )
        return

    elif text == "📞 Support":
        msg = (
            "📞 *Support*\n\n"
            "💰 *Credits:* Used to run SMS tests.\n\n"
            "📩 *SMS Bomber Pricing:*\n"
            "• 2 min = 5 credits\n• 3 min = 8 credits\n"
            "• 4 min = 10 credits\n• 5 min = 12 credits\n\n"
            f"✉️ *Custom SMS:* {CUSTOM_SMS_COST} credits per message\n\n"
            "⭐ *VIP:* Unlimited usage, no credit deduction.\n\n"
            f"💳 *Buy credits/VIP:* Contact @{ADMIN_USERNAME}\n\n"
            "🎟 *Redeem Codes:* Use the Redeem Code menu.\n\n"
            f"👤 *Admin:* @{ADMIN_USERNAME}"
        )
        keyboard = [[InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]]
        await update.message.reply_text(msg, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif text == "⚙️ Admin Panel":
        if not is_admin(user_id):
            await update.message.reply_text("⛔ Unauthorized.")
            return
        await update.message.reply_text(
            "⚙️ *Admin Panel*\n\nSelect an option:",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return

    elif text == "⬅️ Back":
        context.user_data['state'] = None
        context.user_data['admin_state'] = None
        context.user_data['custom_sms_number'] = None
        context.user_data['custom_sms_message'] = None
        await update.message.reply_text("Main Menu", reply_markup=get_main_keyboard(user_id))
        return

    # ============ STATE INPUTS ============
    state = context.user_data.get('state')

    if state == 'awaiting_number':
        joined = await is_user_joined(context, user_id)
        if not joined:
            await send_join_prompt(update, context)
            return
        number = text.strip()
        if not number.isdigit() or len(number) < 10 or len(number) > 15:
            await update.message.reply_text("❌ Invalid phone number. Send a valid number.")
            return
        context.user_data['sms_number'] = number
        context.user_data['state'] = 'awaiting_duration'
        await update.message.reply_text(
            f"📱 Number: `{number}`\n\nSelect duration:",
            parse_mode="Markdown",
            reply_markup=get_duration_keyboard()
        )
        return

    # ============ CUSTOM SMS: NUMBER INPUT ============
    if state == 'custom_sms_number':
        joined = await is_user_joined(context, user_id)
        if not joined:
            await send_join_prompt(update, context)
            return
        number = text.strip()
        if not is_valid_bd_number(number):
            await update.message.reply_text(
                "❌ Invalid Bangladesh mobile number.\n\n"
                "Must be 11 digits starting with 01 (e.g. `017XXXXXXXX`).\n\n"
                "📱 Enter the recipient number again:",
                parse_mode="Markdown"
            )
            return
        context.user_data['custom_sms_number'] = number
        context.user_data['state'] = 'custom_sms_message'
        await update.message.reply_text(
            f"📝 Enter your message\n"
            f"Maximum: {CUSTOM_SMS_MAX_LENGTH} অক্ষর",
            reply_markup=get_back_keyboard()
        )
        return

    # ============ CUSTOM SMS: MESSAGE INPUT ============
    if state == 'custom_sms_message':
        message = text.strip()
        if not message:
            await update.message.reply_text("❌ Message cannot be empty. Please enter your message.")
            return
        if len(message) > CUSTOM_SMS_MAX_LENGTH:
            await update.message.reply_text(
                f"❌ Message too long! Maximum {CUSTOM_SMS_MAX_LENGTH} characters allowed.\n"
                f"Your message: {len(message)} characters"
            )
            return
        context.user_data['custom_sms_message'] = message
        context.user_data['state'] = 'custom_sms_confirm'
        number = context.user_data.get('custom_sms_number')
        await update.message.reply_text(
            f"📱 Number: `{number}`\n"
            f"💬 Message: {message}\n"
            f"💳 Cost: {CUSTOM_SMS_COST} Credits\n\n"
            f"Confirm to send?",
            parse_mode="Markdown",
            reply_markup=get_custom_confirm_keyboard()
        )
        return

    if state == 'awaiting_redeem_code':
        code = text.strip().upper()
        conn = get_db()
        already = conn.execute("SELECT * FROM redeemed_history WHERE user_id = ? AND code = ?",
                               (user_id, code)).fetchone()
        if already:
            conn.close()
            await update.message.reply_text("❌ Already redeemed.", reply_markup=get_main_keyboard(user_id))
            context.user_data['state'] = None
            return
        cdata = conn.execute("SELECT * FROM redeem_codes WHERE code = ?", (code,)).fetchone()
        if not cdata:
            conn.close()
            await update.message.reply_text("❌ Invalid code.", reply_markup=get_main_keyboard(user_id))
            context.user_data['state'] = None
            return
        if cdata['expiry'] and datetime.fromisoformat(cdata['expiry']) < datetime.now():
            conn.close()
            await update.message.reply_text("❌ Code expired.", reply_markup=get_main_keyboard(user_id))
            context.user_data['state'] = None
            return
        if cdata['used_count'] >= cdata['max_uses']:
            conn.close()
            await update.message.reply_text("❌ Usage limit reached.", reply_markup=get_main_keyboard(user_id))
            context.user_data['state'] = None
            return
        conn.execute("UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?", (code,))
        conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (cdata['credits'], user_id))
        conn.execute("INSERT INTO redeemed_history (user_id, code, credits, redeemed_at) VALUES (?, ?, ?, ?)",
                     (user_id, code, cdata['credits'], datetime.now().isoformat()))
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"✅ +{cdata['credits']} credits added!\nBalance: {get_user(user_id)['credits']}",
            reply_markup=get_main_keyboard(user_id)
        )
        context.user_data['state'] = None
        return

    await update.message.reply_text("Use the menu below.", reply_markup=get_main_keyboard(user_id))

# ==================== SMS DURATION CALLBACK ====================
async def sms_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "sms_cancel":
        context.user_data['state'] = None
        await query.edit_message_text("❌ Cancelled.")
        await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                       reply_markup=get_main_keyboard(user_id))
        return

    duration_key = data.replace("sms_dur_", "")
    if duration_key not in SMS_DURATIONS:
        await query.edit_message_text("❌ Invalid duration.")
        return

    duration = SMS_DURATIONS[duration_key]
    number = context.user_data.get('sms_number')
    if not number:
        await query.edit_message_text("❌ Session expired. Start again.")
        return

    u = get_user(user_id)
    vip = is_vip_active(u)

    if not vip:
        if u['credits'] < duration['credits']:
            await query.edit_message_text(
                f"❌ *Insufficient Credits!*\n\nRequired: {duration['credits']}\n"
                f"Your balance: {u['credits']}\n\nBuy more from the menu.",
                parse_mode="Markdown"
            )
            context.user_data['state'] = None
            return
        conn = get_db()
        conn.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?",
                     (duration['credits'], user_id))
        conn.commit()
        conn.close()

    def call_api():
        try:
            r = requests.post(API_URL, json={"number": number, "amount": duration['minutes']},
                              headers={"Content-Type": "application/json"}, timeout=15)
            logger.info(f"API: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"API Error: {e}")

    asyncio.create_task(asyncio.to_thread(call_api))

    conn = get_db()
    conn.execute("UPDATE users SET total_requests = total_requests + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text(
        f"✅ *Request Sent!*\n\n📱 `{number}`\n⏱ {duration['minutes']} min\n"
        f"💳 Credits used: {duration['credits'] if not vip else '0 (VIP)'}",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_IDS[0],
            text=(f"🚨 NEW REQUEST\n\n👤 {u['name']} (@{u['username'] or 'NoUsername'})\n"
                  f"🆔 `{user_id}`\n📱 {number}\n⏱ {duration['minutes']} min\n"
                  f"💳 {duration['credits']}"),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Admin notify: {e}")

    context.user_data['state'] = None
    await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                   reply_markup=get_main_keyboard(user_id))

# ==================== CUSTOM SMS CALLBACK ====================
async def custom_sms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "custom_cancel":
        context.user_data['state'] = None
        context.user_data['custom_sms_number'] = None
        context.user_data['custom_sms_message'] = None
        await query.edit_message_text("❌ Cancelled.")
        await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                       reply_markup=get_main_keyboard(user_id))
        return

    if data == "custom_send":
        # Prevent duplicate sends
        if context.user_data.get('custom_sending'):
            await query.answer("⏳ Already processing...", show_alert=True)
            return

        number = context.user_data.get('custom_sms_number')
        message = context.user_data.get('custom_sms_message')

        if not number or not message:
            await query.edit_message_text("❌ Session expired. Please start again.")
            await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                           reply_markup=get_main_keyboard(user_id))
            return

        u = get_user(user_id)
        if u['credits'] < CUSTOM_SMS_COST:
            await query.edit_message_text(
                f"❌ *Insufficient Credits!*\n\n"
                f"Required: {CUSTOM_SMS_COST} credits\n"
                f"Your balance: {u['credits']} credits\n\n"
                f"Buy more from the menu.",
                parse_mode="Markdown"
            )
            context.user_data['state'] = None
            context.user_data['custom_sms_number'] = None
            context.user_data['custom_sms_message'] = None
            return

        # Lock to prevent duplicate
        context.user_data['custom_sending'] = True

        await query.edit_message_text("⏳ Processing your SMS...")

        # API call (synchronous in thread)
        api_success = False
        api_response = ""

        def call_custom_api():
            nonlocal api_success, api_response
            try:
                url = f"{CUSTOM_SMS_API}?phone={quote(number)}&message={quote(message)}"
                r = requests.get(url, timeout=20)
                api_response = r.text
                logger.info(f"Custom SMS API: {r.status_code} - {r.text}")
                if r.status_code == 200:
                    api_success = True
            except Exception as e:
                logger.error(f"Custom SMS API Error: {e}")
                api_response = str(e)

        await asyncio.to_thread(call_custom_api)

        if api_success:
            # Deduct credits only after success
            conn = get_db()
            conn.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?",
                         (CUSTOM_SMS_COST, user_id))
            conn.execute("UPDATE users SET total_requests = total_requests + 1 WHERE user_id = ?",
                         (user_id,))
            conn.commit()
            new_bal = conn.execute("SELECT credits FROM users WHERE user_id = ?",
                                   (user_id,)).fetchone()['credits']
            conn.close()

            await query.edit_message_text(
                f"✅ *SMS Sent Successfully!*\n\n"
                f"📱 Number: `{number}`\n"
                f"💬 Message: {message}\n"
                f"💳 Credits used: {CUSTOM_SMS_COST}\n"
                f"💰 New balance: {new_bal}",
                parse_mode="Markdown"
            )

            # Notify admin
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_IDS[0],
                    text=(f"✉️ NEW CUSTOM SMS\n\n"
                          f"👤 {u['name']} (@{u['username'] or 'NoUsername'})\n"
                          f"🆔 `{user_id}`\n📱 {number}\n💬 {message}\n"
                          f"💳 {CUSTOM_SMS_COST} credits"),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Admin notify: {e}")
        else:
            await query.edit_message_text(
                f"❌ *Failed to send SMS!*\n\n"
                f"No credits were deducted.\n"
                f"Please try again later.\n\n"
                f"Error: {api_response[:150]}",
                parse_mode="Markdown"
            )

        # Cleanup
        context.user_data['state'] = None
        context.user_data['custom_sms_number'] = None
        context.user_data['custom_sms_message'] = None
        context.user_data['custom_sending'] = False

        await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                       reply_markup=get_main_keyboard(user_id))
        return

# ==================== ADMIN CALLBACKS ====================
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    data = query.data

    if data == "admin_mainmenu":
        context.user_data['admin_state'] = None
        await query.edit_message_text("Returning to Main Menu...")
        await context.bot.send_message(chat_id=user_id, text="Main Menu",
                                       reply_markup=get_main_keyboard(user_id))
        return

    elif data == "admin_stats":
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        vip = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1").fetchone()[0]
        banned = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0]
        reqs = conn.execute("SELECT SUM(total_requests) FROM users").fetchone()[0] or 0
        conn.close()
        await query.edit_message_text(
            f"📊 *Statistics*\n\n👥 Total Users: {total}\n⭐ VIP: {vip}\n🚫 Banned: {banned}\n📩 Requests: {reqs}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        return

    elif data.startswith("admin_userlist_"):
        conn = get_db()
        page = int(data.split("_")[2])
        users = conn.execute("SELECT * FROM users ORDER BY user_id LIMIT 10 OFFSET ?",
                             (page * 10,)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        msg = f"📜 *User List* (Page {page+1}/{(total//10)+1})\n\n"
        for u in users:
            v = "⭐" if u['is_vip'] else "👤"
            b = "🚫" if u['is_banned'] else ""
            msg += f"{v} `{u['user_id']}` — {u['name']} — {u['credits']} cr {b}\n"
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_userlist_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_userlist_{page+1}"))
        kb = []
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
        await query.edit_message_text(msg, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(kb))
        return

    elif data == "admin_addcredit":
        context.user_data['admin_state'] = 'addcredit_id'
        await query.edit_message_text("➕ *Add/Sub Credit*\n\nEnter the Telegram User ID:")
        return

    elif data == "admin_resetcredit":
        context.user_data['admin_state'] = 'resetcredit_id'
        await query.edit_message_text("🧹 *Reset Credit*\n\nEnter the Telegram User ID:")
        return

    elif data == "admin_ban":
        context.user_data['admin_state'] = 'ban_id'
        await query.edit_message_text("🚫 *Ban User*\n\nEnter the Telegram User ID:")
        return

    elif data == "admin_unban":
        context.user_data['admin_state'] = 'unban_id'
        await query.edit_message_text("✅ *Unban User*\n\nEnter the Telegram User ID:")
        return

    elif data == "admin_gencode":
        context.user_data['admin_state'] = 'gencode_credits'
        await query.edit_message_text("🎟 *Generate Code*\n\nEnter credit amount:")
        return

    elif data == "admin_broadcast":
        context.user_data['admin_state'] = 'broadcast_msg'
        await query.edit_message_text("📢 Send the message to broadcast:")
        return

    elif data == "admin_vip":
        context.user_data['admin_state'] = 'vip_id'
        await query.edit_message_text("⭐ *VIP Management*\n\nEnter the Telegram User ID:")
        return

    elif data == "admin_back":
        context.user_data['admin_state'] = None
        await query.edit_message_text("⚙️ Admin Panel", reply_markup=get_admin_keyboard())
        return

# ==================== ADMIN INPUT ====================
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    text = update.message.text
    state = context.user_data.get('admin_state')
    if not state:
        return

    conn = get_db()

    if state == 'addcredit_id':
        try:
            tid = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid User ID.")
            return
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        context.user_data['admin_target'] = tid
        context.user_data['admin_state'] = 'addcredit_amount'
        await update.message.reply_text(
            f"👤 {t['name']}\n💰 Current: {t['credits']}\n\nEnter amount (+ to add, - to subtract):"
        )
        conn.close()
        return

    elif state == 'addcredit_amount':
        try:
            amt = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid amount.")
            return
        tid = context.user_data.get('admin_target')
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        if amt >= 0:
            conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amt, tid))
        else:
            new_bal = max(0, t['credits'] + amt)
            conn.execute("UPDATE users SET credits = ? WHERE user_id = ?", (new_bal, tid))
        conn.commit()
        updated = conn.execute("SELECT credits FROM users WHERE user_id = ?", (tid,)).fetchone()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(
            f"✅ Done!\nUser: `{tid}`\nChange: {amt:+d}\nNew balance: {updated['credits']}",
            parse_mode="Markdown"
        )
        return

    elif state == 'resetcredit_id':
        try:
            tid = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid User ID.")
            return
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        conn.execute("UPDATE users SET credits = 0 WHERE user_id = ?", (tid,))
        conn.commit()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(f"✅ Reset credits for `{tid}` to 0.", parse_mode="Markdown")
        return

    elif state == 'ban_id':
        try:
            tid = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid User ID.")
            return
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (tid,))
        conn.commit()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(f"✅ Banned `{tid}`.", parse_mode="Markdown")
        return

    elif state == 'unban_id':
        try:
            tid = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid User ID.")
            return
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (tid,))
        conn.commit()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(f"✅ Unbanned `{tid}`.", parse_mode="Markdown")
        return

    elif state == 'gencode_credits':
        try:
            cr = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid amount.")
            return
        context.user_data['gencode_credits'] = cr
        context.user_data['admin_state'] = 'gencode_maxuses'
        await update.message.reply_text("Enter max uses:")
        conn.close()
        return

    elif state == 'gencode_maxuses':
        try:
            mu = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid number.")
            return
        cr = context.user_data.get('gencode_credits', 0)
        code = generate_redeem_code()
        conn.execute(
            "INSERT INTO redeem_codes (code, credits, max_uses, created_at) VALUES (?, ?, ?, ?)",
            (code, cr, mu, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(
            f"🎟 *Code Generated!*\n\nCode: `{code}`\nCredits: {cr}\nMax Uses: {mu}",
            parse_mode="Markdown"
        )
        return

    elif state == 'broadcast_msg':
        conn.close()
        context.user_data['admin_state'] = None
        all_u = get_db().execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        ok, fail = 0, 0
        for u in all_u:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=text)
                ok += 1
            except:
                fail += 1
        await update.message.reply_text(f"📢 Done!\n✅ Sent: {ok}\n❌ Failed: {fail}")
        return

    elif state == 'vip_id':
        try:
            tid = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid User ID.")
            return
        t = conn.execute("SELECT * FROM users WHERE user_id = ?", (tid,)).fetchone()
        if not t:
            conn.close()
            await update.message.reply_text("❌ User not found.")
            return
        context.user_data['admin_target'] = tid
        context.user_data['admin_state'] = 'vip_days'
        await update.message.reply_text(
            f"👤 {t['name']}\n⭐ VIP: {'Yes' if t['is_vip'] else 'No'}\n\n"
            f"Enter VIP days (0 to remove):"
        )
        conn.close()
        return

    elif state == 'vip_days':
        try:
            days = int(text)
        except:
            conn.close()
            await update.message.reply_text("❌ Invalid number.")
            return
        tid = context.user_data.get('admin_target')
        if days <= 0:
            conn.execute("UPDATE users SET is_vip = 0, vip_expiry = NULL WHERE user_id = ?", (tid,))
        else:
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
            conn.execute("UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?", (expiry, tid))
        conn.commit()
        conn.close()
        context.user_data['admin_state'] = None
        await update.message.reply_text(f"✅ VIP updated for `{tid}`.", parse_mode="Markdown")
        return

    conn.close()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ==================== MAIN ====================
def main():
    init_db()

    # Start Flask web server in background thread (for Render + UptimeRobot)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Web server started for uptime monitoring")

    # Build Telegram application
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="check_join"))
    application.add_handler(CallbackQueryHandler(sms_duration_callback, pattern="^sms_"))
    application.add_handler(CallbackQueryHandler(custom_sms_callback, pattern="^custom_"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.add_error_handler(error_handler)

    print("🤖 Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
