import telebot
import random
import string
import time
import json
import os
import re
import html

# ==============================
#      CONFIGURATION
# ==============================

import os
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- Admins & Channels ---
MAIN_ADMINS = [7251749429, 7529094605]

# Where to log general data events (can be @username or -100id)
DATA_CHANNEL = "@userdatachnl"

# Force-join gate (kept for redeem flow)
FORCE_JOIN_CHANNEL_ID = -1002805274329
FORCE_JOIN_CHANNEL_LINK = "https://t.me/+pI7fWKuTecxhZDU1"

# === Channels ===
PROOF_CHANNEL_ID = -1003186829689      # must be a chat where the bot is admin
STORE_CHANNEL_ID = -1002893816996      # REQUIRED (storage channel where uploads go)

# --- Data Files (for persistence) ---
DATA_DIR = "/data/"
USERS_FILE = os.path.join(DATA_DIR, "users.txt")
BANNED_USERS_FILE = os.path.join(DATA_DIR, "banned_users.txt")
ADMINS_FILE = os.path.join(DATA_DIR, "admins.txt")
CODES_FILE = os.path.join(DATA_DIR, "codes.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.txt")
FILES_DB_FILE = os.path.join(DATA_DIR, "files_db.json")
BUNDLES_DB_FILE = os.path.join(DATA_DIR, "bundles_db.json")

# ==============================
#      INITIALIZE BOT
# ==============================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
BOT_USERNAME = None  # filled in at startup (for share links)

# ==============================
#   PERSISTENT DATA STORAGE
# ==============================
# codes_db: {code: {
#   "category": str, "account": str,
#   "max_uses": int, "used_count": int,
#   "expires_at": int|None, "created_by": int
# }}
codes_db = {}
users = set()
banned_users = set()
admins = set(MAIN_ADMINS)
categories = ["Movies", "Tools", "Premium", "Netflix", "Amazon Prime", "Crunchyroll", "Redeem Code"]

# Proof screenshot state
pending_proof = {}   # { user_id: {"code": code, "category": category, "expires": timestamp} }

# File/Bundles
# files_db: {
#   code: {
#     "owner": int, "store_msg_id": int, "type": str, "caption": str, "created_at": int,
#     "access": {"mode": "public"|"unlisted"|"private", "limit": int|None, "viewed_by": [int,int,...]}
#   }
# }
files_db = {}
# bundles_db: {
#   code: {
#     "owner": int, "items": [file_code,...], "created_at": int,
#     "access": {"mode": "public"|"unlisted"|"private", "limit": int|None, "viewed_by": [int,int,...]}
#   }
# }
bundles_db = {}
# in-memory bundle sessions: { user_id: [file_code,...] }
bundle_sessions = {}

# Pending flows
pending_add = {}  # kept for legacy; now open to all users
# Redeem creation wizard:
# pending_redeem[user_id] = {"stage": "choose_cat"|"have_cat"|"await_code"|"await_time"|"await_limit",
#                            "category": str|None, "accounts": [str...] }
pending_redeem = {}
# Unlisted count entry for privacy change
# pending_privacy[user_id] = {"kind": "file"|"bundle", "code": str}
pending_privacy = {}

# ==============================
#   LOAD / SAVE HELPERS
# ==============================
def load_data():
    global users, banned_users, admins, codes_db, categories, files_db, bundles_db
    try:
        with open(USERS_FILE, "r") as f:
            users = {int(line.strip()) for line in f if line.strip()}
    except FileNotFoundError:
        print(f"'{USERS_FILE}' not found. Starting with empty user list.")

    try:
        with open(BANNED_USERS_FILE, "r") as f:
            banned_users = {int(line.strip()) for line in f if line.strip()}
    except FileNotFoundError:
        print(f"'{BANNED_USERS_FILE}' not found. Starting with no banned users.")

    try:
        with open(ADMINS_FILE, "r") as f:
            for line in f:
                v = line.strip()
                if v:
                    admins.add(int(v))
    except FileNotFoundError:
        print(f"'{ADMINS_FILE}' not found. Starting with only MAIN_ADMINS.")

    try:
        with open(CODES_FILE, "r") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                # migrate old schema (used->max_uses==1 etc.)
                for k, v in loaded.items():
                    if isinstance(v, dict):
                        v.setdefault("max_uses", 1 if v.get("used") is not None else 1)
                        v.setdefault("used_count", 1 if v.get("used") else 0)
                        v.pop("used", None)
                        v.setdefault("expires_at", None)
                        v.setdefault("created_by", 0)
                codes_db.update(loaded)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"'{CODES_FILE}' not found or invalid. Starting with empty codes DB.")

    try:
        with open(CATEGORIES_FILE, "r") as f:
            loaded_categories = [line.strip() for line in f if line.strip()]
            if loaded_categories:
                categories[:] = loaded_categories
    except FileNotFoundError:
        print(f"'{CATEGORIES_FILE}' not found. Using default categories.")

    try:
        with open(FILES_DB_FILE, "r") as f:
            tmp = json.load(f)
            if isinstance(tmp, dict):
                # migrate access block
                for code, entry in tmp.items():
                    entry.setdefault("access", {"mode": "public", "limit": None, "viewed_by": []})
                    entry["access"].setdefault("viewed_by", [])
                files_db.update(tmp)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"'{FILES_DB_FILE}' not found/invalid. Starting empty.")

    try:
        with open(BUNDLES_DB_FILE, "r") as f:
            tmp = json.load(f)
            if isinstance(tmp, dict):
                for code, entry in tmp.items():
                    entry.setdefault("access", {"mode": "public", "limit": None, "viewed_by": []})
                    entry["access"].setdefault("viewed_by", [])
                bundles_db.update(tmp)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"'{BUNDLES_DB_FILE}' not found/invalid. Starting empty.")

def save_to_file(file_path, data_set):
    with open(file_path, "w") as f:
        for item in sorted(data_set):
            f.write(str(item) + "\n")

def save_codes_db():
    with open(CODES_FILE, "w") as f:
        json.dump(codes_db, f, indent=4)

def save_categories():
    with open(CATEGORIES_FILE, "w") as f:
        for cat in categories:
            f.write(cat + "\n")

def save_files_db():
    with open(FILES_DB_FILE, "w") as f:
        json.dump(files_db, f, indent=2)

def save_bundles_db():
    with open(BUNDLES_DB_FILE, "w") as f:
        json.dump(bundles_db, f, indent=2)

# ==============================
#      UTILS & HELPERS
# ==============================
def generate_code(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def generate_unique_code():
    while True:
        code = generate_code(10)
        if (code not in codes_db) and (code not in files_db) and (code not in bundles_db):
            return code

def has_joined_channel(user_id):
    try:
        member_status = bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL_ID, user_id=user_id).status
        return member_status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Error checking user {user_id}: {e}")
        return False

def display_name(u):
    first = (u.first_name or "").strip()
    last = (u.last_name or "").strip()
    uname = f"@{u.username}" if u.username else "—"
    full = (first + " " + last).strip() or "—"
    return f"{full} ({uname})"

def safe_html(s: str) -> str:
    return html.escape(s or "")

def readable_time(ts: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except:
        return str(ts)

def content_supports_caption(ctype: str) -> bool:
    return ctype in {"animation", "audio", "document", "photo", "video", "voice"}

def build_share_link(code: str) -> str:
    username = BOT_USERNAME or "YourBot"
    return f"https://t.me/{username}?start={code}"

def send_to_data_channel(text: str):
    try:
        bot.send_message(DATA_CHANNEL, text, parse_mode=None, disable_web_page_preview=True)
    except Exception as e:
        print("DATA_CHANNEL send failed:", e)

def build_store_caption_html(user, code: str, created_at: int, original_caption: str, ctype: str) -> str:
    user_line = safe_html(display_name(user))
    uid_line = safe_html(str(user.id))
    t_line = safe_html(readable_time(created_at))
    orig = safe_html(original_caption) if (original_caption and content_supports_caption(ctype)) else ""
    link = safe_html(build_share_link(code))
    parts = []
    if orig:
        parts.append(orig)
    parts.append("<b>📥 Uploaded via bot</b>")
    parts.append(f"👤 <b>User:</b> {user_line}")
    parts.append(f"🆔 <b>User ID:</b> <code>{uid_line}</code>")
    parts.append(f"🕒 <b>Time:</b> <code>{t_line}</code>")
    parts.append(f"🔗 <b>Share:</b> <code>{link}</code>")
    return "\n".join(parts)

def is_deeplink(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(?:https?://)?t\.me/[^?\s]+?\?start=([A-Za-z0-9_-]+)", text)
    return m.group(1) if m else ""

def access_ok_for_file(entry, requester_id: int) -> (bool, str):
    acc = entry.get("access", {"mode": "public", "limit": None, "viewed_by": []})
    mode = acc.get("mode", "public")
    if requester_id == entry.get("owner"):
        return True, ""
    if mode == "public":
        return True, ""
    if mode == "private":
        return False, "🔒 This file is Private. Only the owner can access."
    if mode == "unlisted":
        limit = acc.get("limit")
        viewed_by = set(acc.get("viewed_by") or [])
        if requester_id in viewed_by:
            return True, ""
        # if limit not set => treat like public-unlisted (no cap)
        if limit is None:
            return True, ""
        # still capacity?
        if len(viewed_by) < int(limit):
            return True, ""
        return False, "🚫 This Unlisted link has reached its viewer limit."
    return True, ""

def record_view(entry, requester_id: int, saver):
    acc = entry.setdefault("access", {"mode": "public", "limit": None, "viewed_by": []})
    mode = acc.get("mode", "public")
    if mode != "unlisted":
        return
    viewed_by = set(acc.get("viewed_by") or [])
    if requester_id not in viewed_by:
        viewed_by.add(requester_id)
        acc["viewed_by"] = list(viewed_by)
        saver()

# ===== Proof helpers =====
def proof_caption_html(u, uid, code, category):
    name_safe = safe_html(display_name(u))
    uid_safe = safe_html(str(uid))
    code_safe = safe_html(str(code))
    cat_safe = safe_html(str(category))
    return (
        "<b>🖼 Proof Screenshot Received</b>\n\n"
        f"👤 <b>Name:</b> {name_safe}\n"
        f"🆔 <b>User ID:</b> <code>{uid_safe}</code>\n"
        f"🔑 <b>Code:</b> <code>{code_safe}</code>\n"
        f"📂 <b>Category:</b> {cat_safe}\n"
        f"🕒 <b>Time:</b> <code>{readable_time(int(time.time()))}</code>"
    )

def set_pending_proof(user_id, code, category, ttl_seconds=600):
    pending_proof[user_id] = {"code": code, "category": category, "expires": time.time() + ttl_seconds}

def has_pending_proof(user_id) -> bool:
    ctx = pending_proof.get(user_id)
    return bool(ctx and time.time() <= ctx.get("expires", 0))

def get_and_prune_pending_proof(user_id):
    ctx = pending_proof.get(user_id)
    if not ctx:
        return None
    if time.time() > ctx.get("expires", 0):
        pending_proof.pop(user_id, None)
        return None
    return ctx

def clear_pending_proof(user_id):
    pending_proof.pop(user_id, None)

def try_send_proof_via_copy(message, caption_html):
    try:
        bot.copy_message(
            chat_id=PROOF_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            caption=caption_html,
            parse_mode="HTML"
        )
        return True, None
    except Exception as e:
        return False, e

def try_send_proof_via_file_id_photo(photo_file_id, caption_html):
    try:
        bot.send_photo(PROOF_CHANNEL_ID, photo_file_id, caption=caption_html, parse_mode="HTML")
        return True, None
    except Exception as e:
        return False, e

def try_send_proof_via_file_id_doc(file_id, caption_html):
    try:
        bot.send_document(PROOF_CHANNEL_ID, file_id, caption=caption_html, parse_mode="HTML")
        return True, None
    except Exception as e:
        return False, e

def try_download_and_reupload(file_id, caption_html, filename_prefix="proof"):
    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        ext = os.path.splitext(file_info.file_path)[1] or ".jpg"
        local_path = f"{filename_prefix}_{int(time.time())}{ext}"
        with open(local_path, "wb") as f:
            f.write(downloaded)
        if ext.lower() in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
            with open(local_path, "rb") as f:
                bot.send_photo(PROOF_CHANNEL_ID, f, caption=caption_html, parse_mode="HTML")
        else:
            with open(local_path, "rb") as f:
                bot.send_document(PROOF_CHANNEL_ID, f, caption=caption_html, parse_mode="HTML")
        try:
            os.remove(local_path)
        except:
            pass
        return True, None
    except Exception as e:
        return False, e

def explain_send_error(e):
    msg = str(e)
    return (
        "❌ Failed to send your screenshot to the channel.\n\n"
        "Possible causes:\n"
        "• Bot is not added as *Admin* in the channel\n"
        "• Bot lacks permission to send messages/media\n"
        "• Channel ID is incorrect or private\n\n"
        f"_Technical detail:_ `{msg}`"
    )

# ==============================
#      COMMANDS: START/HELP
# ==============================
@bot.message_handler(commands=["start"])
def start_cmd(message):
    user_id = message.from_user.id
    payload_code = ""
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        payload_code = parts[1].strip()

    if user_id in banned_users:
        return bot.send_message(user_id, "🚫 You are banned from using this bot.")

    # Public retrieval via deep link (no force-join)
    if payload_code and (payload_code in files_db or payload_code in bundles_db):
        serve_file_by_code(user_id, payload_code)
        return

    # If redeem/category UI flow: show categories list immediately
    if user_id not in users:
        users.add(user_id)
        save_to_file(USERS_FILE, users)
        text = f"🆕 New User Notification\nUser: {message.from_user.first_name} (@{message.from_user.username})\nUser ID: {user_id}"
        send_to_data_channel(text)

    # Show Category chooser (no force-join needed to create codes, but required to REDEEM later)
    markup = telebot.types.InlineKeyboardMarkup()
    for cat in categories:
        markup.add(telebot.types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
    bot.send_message(
        user_id,
        "👋 Welcome!\n\n"
        "• Upload any file to get a share link (with privacy controls).\n"
        "• Use /bundle to create a multi-file bundle.\n\n"
        "🧩 **Create Redeem**\nChoose a category to add account(s), then pick a code type:",
        reply_markup=markup
    )

@bot.message_handler(commands=["help"])
def help_cmd(message):
    uid = message.from_user.id
    main_admin_block = (
        "👑 **Main Admin Commands**\n"
        "/addadmin `<user_id>` - Add new admin\n"
        "/adminlist - Show all admins\n"
        "/addcat `<Category Name>` - Add a new category\n"
        "/delcat `<Category Name>` - Delete a category\n"
        "/broadcast `<message>` - (Admins only) Send message to all users\n"
        "/stats - (Admins only) Show bot stats\n"
        "/ban `<user_id>` - Ban user\n"
        "/unban `<user_id>` - Unban user\n"
    )
    users_adminish_block = (
        "🛠️ **Users Commands**\n"
        "/add - Add Accounts (normal users can use)\n"
    )
    user_file_block = (
        "📁 **File Commands**\n"
        "/bundle - Start bundling multiple files\n"
        "/finish - Create bundle link (then set privacy)\n"
        "/cancel - Cancel bundling\n"
        "/myfiles - List your uploaded files (with privacy buttons)\n\n"
        "/add - Add Accounts (normal users can use)\n"
        f"• Retrieve via `https://t.me/{BOT_USERNAME or 'YourBot'}?start=CODE` or by sending the CODE."
    )

    if uid in MAIN_ADMINS:
        text = f"{main_admin_block}\n{users_adminish_block}\n{user_file_block}"
    elif uid in admins:
        text = f"{users_adminish_block}\n{user_file_block}"
    else:
        text = (
            "👋 **Welcome!**\n\n"
            "**Start & Help**\n"
            "/start to begin, /help for full command list.\n\n" + user_file_block
        )
    bot.send_message(message.chat.id, text)

# ==============================
#           ADMIN COMMANDS
# ==============================
@bot.message_handler(commands=["stats"])
def stats_cmd(message):
    if message.from_user.id not in admins:
        return
    total_users = len(users)
    total_codes = len(codes_db)
    total_files = len(files_db)
    total_bundles = len(bundles_db)
    total_uses = sum(v.get("used_count", 0) for v in codes_db.values())
    text = (
        "📊 **Bot Statistics**\n\n"
        f"👥 Total Users: {total_users}\n"
        f"🔑 Total Codes: {total_codes}\n"
        f"📈 Total Redeems: {total_uses}\n\n"
        f"🗂 Stored Files: {total_files}\n"
        f"🧺 Bundles: {total_bundles}"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["addadmin"])
def add_admin(message):
    if message.from_user.id not in MAIN_ADMINS:
        return
    try:
        uid = int(message.text.split()[1])
        admins.add(uid)
        save_to_file(ADMINS_FILE, admins - set(MAIN_ADMINS))
        bot.send_message(message.chat.id, f"✅ Added `{uid}` as admin.")
    except:
        bot.send_message(message.chat.id, "⚠️ Usage: `/addadmin user_id`")

@bot.message_handler(commands=["adminlist"])
def admin_list(message):
    if message.from_user.id not in admins:
        return
    text = "👑 **Main Admins:**\n"
    for admin_id in MAIN_ADMINS:
        text += f"- `{admin_id}`\n"
    text += "\n🔰 **Other Admins:**\n"
    other_admins = admins - set(MAIN_ADMINS)
    if other_admins:
        for admin_id in other_admins:
            text += f"- `{admin_id}`\n"
    else:
        text += "- None"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["ban", "unban"])
def ban_unban_user(message):
    if message.from_user.id not in admins:
        return
    try:
        command, uid_str = message.text.split()
        uid = int(uid_str)
        if command == "/ban":
            banned_users.add(uid)
            save_to_file(BANNED_USERS_FILE, banned_users)
            bot.send_message(message.chat.id, f"🚫 Banned user `{uid}`")
        elif command == "/unban":
            banned_users.discard(uid)
            save_to_file(BANNED_USERS_FILE, banned_users)
            bot.send_message(message.chat.id, f"✅ Unbanned user `{uid}`")
    except:
        bot.send_message(message.chat.id, "⚠️ Usage: `/ban user_id` OR `/unban user_id`")

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if message.from_user.id not in admins:
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        return bot.send_message(message.chat.id, "⚠️ Please provide a message: `/broadcast YourMessage`")
    sent_count, failed_count = 0, 0
    for uid in list(users):
        try:
            bot.send_message(uid, f"📢 **Broadcast:**\n\n{text}")
            sent_count += 1
            time.sleep(0.1)
        except Exception as e:
            failed_count += 1
            print(f"Failed to send broadcast to {uid}: {e}")
    bot.send_message(message.chat.id, f"✅ Broadcast finished.\n\n📬 Sent to: {sent_count} users\n❌ Failed for: {failed_count} users")

# ==============================
#         REDEEM CREATION
#   (/start flow + /add for users)
# ==============================
def show_code_type_buttons(chat_id):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("✨ Custom Redeem Code", callback_data="code_type_custom"),
    )
    kb.add(
        telebot.types.InlineKeyboardButton("⏳ Time Code", callback_data="code_type_time"),
        telebot.types.InlineKeyboardButton("👥 User Limit Code", callback_data="code_type_limit"),
    )
    bot.send_message(chat_id,
                     "Choose code type:\n\n"
                     "• **Custom**: you pick the code text (e.g., `FESTIVE2025`).\n"
                     "• **Time**: code auto-expires after hours you set.\n"
                     "• **User Limit**: only first N users can redeem.",
                     reply_markup=kb)

@bot.message_handler(commands=["add"])
def add_cmd(message):
    """Open to ALL users now (not only admins)."""
    if message.from_user.id in banned_users:
        return
    # Start like /start category chooser
    markup = telebot.types.InlineKeyboardMarkup()
    for cat in categories:
        markup.add(telebot.types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
    bot.send_message(message.chat.id, "Choose a *category* to add account(s):", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cat_"))
def handle_choose_category(call):
    if call.from_user.id in banned_users:
        return
    cat = call.data.split("_", 1)[1]
    pending_redeem[call.from_user.id] = {"stage": "have_cat", "category": cat, "accounts": []}
    bot.edit_message_text(f"📂 **Category:** {cat}\n\nNow send the *account detail(s)* (one per line).",
                          call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: pending_redeem.get(m.from_user.id, {}).get("stage") == "have_cat", content_types=['text'])
def receive_accounts_for_redeem(message):
    ctx = pending_redeem.get(message.from_user.id)
    if not ctx: return
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    if not lines:
        return bot.send_message(message.chat.id, "⚠️ Send at least one non-empty line.")
    ctx["accounts"] = lines
    show_code_type_buttons(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("code_type_"))
def handle_code_type(call):
    uid = call.from_user.id
    ctx = pending_redeem.get(uid)
    if not ctx:
        try: bot.answer_callback_query(call.id, "Session expired. Use /add again.")
        except: pass
        return
    kind = call.data.split("_", 2)[2]
    if kind == "custom":
        ctx["stage"] = "await_code"
        bot.edit_message_text("✍️ Send your **custom code text** (letters/digits/`-`/`_`, 4–24 chars).",
                              call.message.chat.id, call.message.message_id)
    elif kind == "time":
        ctx["stage"] = "await_time"
        bot.edit_message_text("⏳ Send expiry in **hours** (e.g., `2` for 2 hours).",
                              call.message.chat.id, call.message.message_id)
    elif kind == "limit":
        ctx["stage"] = "await_limit"
        bot.edit_message_text("👥 Send **user limit** as a number (e.g., `100`).",
                              call.message.chat.id, call.message.message_id)

def make_codes_and_reply(chat_id, creator_id, category, accounts, max_uses=1, expires_at=None, custom_code=None):
    made = []
    if custom_code:
        code = custom_code
        if code in codes_db or code in files_db or code in bundles_db:
            bot.send_message(chat_id, "❌ This code already exists. Choose another.")
            return
        if len(accounts) > 1:
            bot.send_message(chat_id, "⚠️ Custom code will be created for the *first* account only (one code).")
        acc = accounts[0]
        codes_db[code] = {
            "category": category, "account": acc,
            "max_uses": max_uses, "used_count": 0,
            "expires_at": expires_at, "created_by": creator_id
        }
        made.append(code)
    else:
        for acc in accounts:
            code = generate_unique_code()
            codes_db[code] = {
                "category": category, "account": acc,
                "max_uses": max_uses, "used_count": 0,
                "expires_at": expires_at, "created_by": creator_id
            }
            made.append(code)
    save_codes_db()
    lines = [f"🔑 `{c}`" for c in made]
    meta = []
    if expires_at: meta.append(f"⏳ Expires: {readable_time(expires_at)}")
    if max_uses != 1: meta.append(f"👥 Limit: {max_uses} uses")
    meta_txt = (" (" + ", ".join(meta) + ")") if meta else ""
    bot.send_message(chat_id, f"✅ Created **{len(made)}** code(s){meta_txt} for **{category}**:\n" + "\n".join(lines))

@bot.message_handler(func=lambda m: pending_redeem.get(m.from_user.id, {}).get("stage") == "await_code", content_types=['text'])
def finalize_custom_code(message):
    uid = message.from_user.id
    ctx = pending_redeem.pop(uid, None)
    if not ctx: return
    code = message.text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,24}", code):
        bot.send_message(message.chat.id, "❌ Invalid format. Use letters/digits/`-`/`_` (4–24 chars).")
        pending_redeem[uid] = ctx; ctx["stage"] = "await_code"
        return
    make_codes_and_reply(message.chat.id, uid, ctx["category"], ctx["accounts"], max_uses=1, expires_at=None, custom_code=code)

@bot.message_handler(func=lambda m: pending_redeem.get(m.from_user.id, {}).get("stage") == "await_time", content_types=['text'])
def finalize_time_code(message):
    uid = message.from_user.id
    ctx = pending_redeem.pop(uid, None)
    if not ctx: return
    try:
        hours = int(message.text.strip())
        if hours <= 0: raise ValueError()
    except:
        bot.send_message(message.chat.id, "❌ Please send a positive integer (hours).")
        pending_redeem[uid] = ctx; ctx["stage"] = "await_time"
        return
    expires_at = int(time.time()) + hours * 3600
    make_codes_and_reply(message.chat.id, uid, ctx["category"], ctx["accounts"], max_uses=999999999, expires_at=expires_at)

@bot.message_handler(func=lambda m: pending_redeem.get(m.from_user.id, {}).get("stage") == "await_limit", content_types=['text'])
def finalize_limit_code(message):
    uid = message.from_user.id
    ctx = pending_redeem.pop(uid, None)
    if not ctx: return
    try:
        lim = int(message.text.strip())
        if lim <= 0: raise ValueError()
    except:
        bot.send_message(message.chat.id, "❌ Please send a positive integer (user limit).")
        pending_redeem[uid] = ctx; ctx["stage"] = "await_limit"
        return
    make_codes_and_reply(message.chat.id, uid, ctx["category"], ctx["accounts"], max_uses=lim, expires_at=None)

# ==============================
#     PROOF SCREENSHOT HANDLERS
# ==============================
@bot.callback_query_handler(func=lambda call: call.data.startswith("proof_"))
def handle_proof_click(call):
    user_id = call.from_user.id
    if user_id in banned_users:
        return

    code = call.data.split("_", 1)[1]
    if code not in codes_db:
        try: bot.answer_callback_query(call.id, "⚠️ This code is not valid.")
        except: pass
        return

    category = codes_db[code]["category"]
    set_pending_proof(user_id, code, category)

    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass

    try:
        bot.answer_callback_query(call.id, "Now send your screenshot as a *photo*.")
    except:
        pass

    bot.send_message(user_id, "✅ Please send your **proof screenshot** now (send as *photo*, not file).")

@bot.message_handler(func=lambda m: m.content_type == 'photo' and has_pending_proof(m.from_user.id), content_types=['photo'])
def receive_proof_photo(message):
    user_id = message.from_user.id
    ctx = get_and_prune_pending_proof(user_id)
    if not ctx:
        return
    code = ctx["code"]
    category = ctx["category"]
    caption_html = proof_caption_html(message.from_user, user_id, code, category)

    ok, err = try_send_proof_via_copy(message, caption_html)
    if ok:
        clear_pending_proof(user_id)
        return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")

    photo_file_id = None
    try:
        photo_file_id = message.photo[-1].file_id
    except Exception:
        pass
    if photo_file_id:
        ok2, err2 = try_send_proof_via_file_id_photo(photo_file_id, caption_html)
        if ok2:
            clear_pending_proof(user_id)
            return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")
    else:
        err2 = "No photo file_id available."

    if photo_file_id:
        ok3, err3 = try_download_and_reupload(photo_file_id, caption_html)
        if ok3:
            clear_pending_proof(user_id)
            return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")
    else:
        err3 = "No file_id to download."

    clear_pending_proof(user_id)
    bot.send_message(user_id, explain_send_error(err3 or err2 or err))

@bot.message_handler(func=lambda m: m.content_type == 'document' and has_pending_proof(m.from_user.id), content_types=['document'])
def receive_proof_document(message):
    user_id = message.from_user.id
    ctx = get_and_prune_pending_proof(user_id)
    if not ctx:
        return

    mime = (message.document.mime_type or "").lower()
    name = (message.document.file_name or "").lower()
    is_image = mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp"))
    if not is_image:
        return bot.send_message(user_id, "⚠️ Please send an *image* as a photo or image file.")

    code = ctx["code"]
    category = ctx["category"]
    caption_html = proof_caption_html(message.from_user, user_id, code, category)

    ok, err = try_send_proof_via_copy(message, caption_html)
    if ok:
        clear_pending_proof(user_id)
        return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")

    file_id = message.document.file_id
    ok2, err2 = try_send_proof_via_file_id_doc(file_id, caption_html)
    if ok2:
        clear_pending_proof(user_id)
        return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")

    ok3, err3 = try_download_and_reupload(file_id, caption_html, filename_prefix="proof_doc")
    if ok3:
        clear_pending_proof(user_id)
        return bot.send_message(user_id, "✅ Your screenshot has been sent to the channel. Thank you!")

    clear_pending_proof(user_id)
    bot.send_message(user_id, explain_send_error(err3 or err2 or err))

# ==============================
#       PUBLIC FILE FEATURES
# ==============================
def privacy_keyboard(kind: str, code: str):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("🌍 Public", callback_data=f"privacy:{kind}:{code}:public"),
        telebot.types.InlineKeyboardButton("🔗 Unlisted", callback_data=f"privacy:{kind}:{code}:unlisted"),
        telebot.types.InlineKeyboardButton("🔒 Private", callback_data=f"privacy:{kind}:{code}:private"),
    )
    return kb

@bot.message_handler(commands=["bundle"])
def bundle_start(message):
    if message.from_user.id in banned_users:
        return
    bundle_sessions[message.from_user.id] = []
    bot.send_message(message.chat.id, "🧺 Bundle mode ON.\nSend files now. When done, use /finish to create one link.\nUse /cancel to exit without saving.")

@bot.message_handler(commands=["cancel"])
def bundle_cancel(message):
    if message.from_user.id in banned_users:
        return
    if bundle_sessions.pop(message.from_user.id, None) is not None:
        bot.send_message(message.chat.id, "✅ Bundle cancelled.")
    else:
        bot.send_message(message.chat.id, "ℹ️ You were not bundling anything.")

@bot.message_handler(commands=["finish"])
def bundle_finish(message):
    if message.from_user.id in banned_users:
        return
    items = bundle_sessions.get(message.from_user.id)
    if not items:
        return bot.send_message(message.chat.id, "⚠️ No files in your bundle. Use /bundle then upload files.")
    code = generate_unique_code()
    bundles_db[code] = {
        "owner": message.from_user.id,
        "items": items[:],
        "created_at": int(time.time()),
        "access": {"mode": "public", "limit": None, "viewed_by": []}
    }
    save_bundles_db()
    bundle_sessions.pop(message.from_user.id, None)
    link = build_share_link(code)
    bot.send_message(
        message.chat.id,
        f"✅ Bundle created with **{len(items)}** file(s).\n🔗 Share link:\n`{link}`\n\n"
        "Set bundle privacy:",
        reply_markup=privacy_keyboard("bundle", code)
    )

@bot.message_handler(commands=["myfiles"])
def myfiles_cmd(message):
    uid = message.from_user.id
    if uid in banned_users:
        return
    user_files = [(c, e) for c, e in files_db.items() if e.get("owner") == uid]
    user_bundles = [(c, e) for c, e in bundles_db.items() if e.get("owner") == uid]

    if not user_files and not user_bundles:
        return bot.send_message(message.chat.id, "📭 You have no uploads yet. Send any file to get a link.")

    # Show up to 20 with per-item privacy buttons
    count = 0
    for code, entry in sorted(user_files, key=lambda x: x[1].get("created_at", 0), reverse=True)[:20]:
        acc = entry.get("access", {})
        mode = acc.get("mode", "public")
        lim = acc.get("limit")
        bot.send_message(
            message.chat.id,
            f"🗂 **File** `{code}` ({entry.get('type')}) — {readable_time(entry.get('created_at', 0))}\n"
            f"🔗 {build_share_link(code)}\n"
            f"🔒 Privacy: *{mode}*" + (f" (limit {lim})" if mode == "unlisted" and lim else ""),
            reply_markup=privacy_keyboard("file", code)
        )
        count += 1
    for code, entry in sorted(user_bundles, key=lambda x: x[1].get("created_at", 0), reverse=True)[:20]:
        acc = entry.get("access", {})
        mode = acc.get("mode", "public")
        lim = acc.get("limit")
        bot.send_message(
            message.chat.id,
            f"📦 **Bundle** `{code}` ({len(entry.get('items', []))} items) — {readable_time(entry.get('created_at', 0))}\n"
            f"🔗 {build_share_link(code)}\n"
            f"🔒 Privacy: *{mode}*" + (f" (limit {lim})" if mode == "unlisted" and lim else ""),
            reply_markup=privacy_keyboard("bundle", code)
        )
        count += 1
    if count == 0:
        bot.send_message(message.chat.id, "📭 Nothing to show yet.")

def serve_file_by_code(chat_id: int, code: str):
    # Single file
    if code in files_db:
        entry = files_db[code]
        ok, reason = access_ok_for_file(entry, chat_id)
        if not ok:
            bot.send_message(chat_id, reason)
            return
        try:
            bot.copy_message(chat_id=chat_id, from_chat_id=STORE_CHANNEL_ID, message_id=entry["store_msg_id"])
            record_view(entry, chat_id, save_files_db)
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Failed to fetch file for `{code}`.\n`{e}`")
            return
        bot.send_message(chat_id, f"🔗 Share link:\n`{build_share_link(code)}`")
        save_files_db()
        return

    # Bundle
    if code in bundles_db:
        bundle = bundles_db[code]
        ok, reason = access_ok_for_file(bundle, chat_id)  # reuse checker
        if not ok:
            bot.send_message(chat_id, reason)
            return
        items = bundle.get("items", [])
        if not items:
            bot.send_message(chat_id, "⚠️ This bundle is empty.")
            return
        bot.send_message(chat_id, f"📦 Sending *{len(items)}* item(s) from bundle `{code}` …")
        sent_any = False
        for c in items:
            if c in files_db:
                entry = files_db[c]
                try:
                    bot.copy_message(chat_id=chat_id, from_chat_id=STORE_CHANNEL_ID, message_id=entry["store_msg_id"])
                    sent_any = True
                except Exception as e:
                    bot.send_message(chat_id, f"⚠️ Failed on item `{c}`: `{e}`")
                    continue
        if sent_any:
            record_view(bundle, chat_id, save_bundles_db)
            bot.send_message(chat_id, f"🔗 Bundle link:\n`{build_share_link(code)}`")
            save_bundles_db()
        return

    bot.send_message(chat_id, "❌ Invalid link/code.\nSend /help for usage.")

@bot.message_handler(func=lambda m: (m.content_type == 'text') and (
    is_deeplink(m.text) or (m.text.strip() in files_db) or (m.text.strip() in bundles_db)
))
def retrieve_by_link_or_code(message):
    if message.from_user.id in banned_users:
        return
    code = is_deeplink(message.text) or message.text.strip()
    serve_file_by_code(message.chat.id, code)

def prompt_privacy_set(chat_id, kind, code):
    kb = privacy_keyboard(kind, code)
    bot.send_message(chat_id, "Set privacy for this item:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("privacy:"))
def handle_privacy_click(call):
    uid = call.from_user.id
    _, kind, code, mode = call.data.split(":")
    if kind == "file":
        entry = files_db.get(code)
        if not entry:
            return
        if entry.get("owner") != uid and uid not in admins:
            bot.answer_callback_query(call.id, "You can't change privacy for this item.")
            return
        if mode == "unlisted":
            pending_privacy[uid] = {"kind": "file", "code": code}
            bot.answer_callback_query(call.id, "Send viewer limit number for Unlisted (e.g., 50).")
            bot.send_message(call.message.chat.id, "🔗 Send **viewer limit** for *Unlisted* (or `0` for unlimited).")
            return
        entry["access"]["mode"] = mode
        if mode != "unlisted":
            entry["access"]["limit"] = None
            entry["access"]["viewed_by"] = []
        save_files_db()
        try: bot.answer_callback_query(call.id, f"Privacy set to {mode}.")
        except: pass
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"✅ File `{code}` privacy: *{mode}*")
    else:
        entry = bundles_db.get(code)
        if not entry:
            return
        if entry.get("owner") != uid and uid not in admins:
            bot.answer_callback_query(call.id, "You can't change privacy for this bundle.")
            return
        if mode == "unlisted":
            pending_privacy[uid] = {"kind": "bundle", "code": code}
            bot.answer_callback_query(call.id, "Send viewer limit number for Unlisted (e.g., 100).")
            bot.send_message(call.message.chat.id, "🔗 Send **viewer limit** for *Unlisted* (or `0` for unlimited).")
            return
        entry["access"]["mode"] = mode
        if mode != "unlisted":
            entry["access"]["limit"] = None
            entry["access"]["viewed_by"] = []
        save_bundles_db()
        try: bot.answer_callback_query(call.id, f"Privacy set to {mode}.")
        except: pass
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_message(call.message.chat.id, f"✅ Bundle `{code}` privacy: *{mode}*")

@bot.message_handler(func=lambda m: pending_privacy.get(m.from_user.id) is not None, content_types=['text'])
def receive_unlisted_limit(message):
    uid = message.from_user.id
    ctx = pending_privacy.pop(uid, None)
    if not ctx: return
    try:
        n = int(message.text.strip())
        if n < 0: raise ValueError()
    except:
        bot.send_message(message.chat.id, "❌ Send a non-negative integer. Try again by tapping Unlisted.")
        return
    if ctx["kind"] == "file":
        entry = files_db.get(ctx["code"])
        if not entry: return
        entry["access"]["mode"] = "unlisted"
        entry["access"]["limit"] = None if n == 0 else n
        entry["access"]["viewed_by"] = []
        save_files_db()
        bot.send_message(message.chat.id, f"✅ File `{ctx['code']}` set to *Unlisted* (limit: {'unlimited' if n==0 else n}).")
    else:
        entry = bundles_db.get(ctx["code"])
        if not entry: return
        entry["access"]["mode"] = "unlisted"
        entry["access"]["limit"] = None if n == 0 else n
        entry["access"]["viewed_by"] = []
        save_bundles_db()
        bot.send_message(message.chat.id, f"✅ Bundle `{ctx['code']}` set to *Unlisted* (limit: {'unlimited' if n==0 else n}).")

# ---- Generic uploads ----
def after_upload_privacy_prompt(chat_id, code, kind):
    kb = privacy_keyboard(kind, code)
    bot.send_message(
        chat_id,
        "Select privacy for this upload:\n"
        "• 🌍 **Public**: anyone with link/code can access.\n"
        "• 🔗 **Unlisted**: set a viewer limit (first N unique users).\n"
        "• 🔒 **Private**: only you.",
        reply_markup=kb
    )

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio', 'sticker', 'voice', 'animation'])
def handle_public_upload(message):
    uid = message.from_user.id
    if uid in banned_users:
        return
    if has_pending_proof(uid):
        return

    ctype = message.content_type
    created_at = int(time.time())
    original_caption = getattr(message, "caption", None)

    code = generate_unique_code()
    caption_html = build_store_caption_html(message.from_user, code, created_at, original_caption, ctype)

    try:
        if content_supports_caption(ctype):
            copied = bot.copy_message(
                chat_id=STORE_CHANNEL_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=caption_html,
                parse_mode="HTML"
            )
            store_msg_id = copied.message_id
        else:
            copied = bot.copy_message(
                chat_id=STORE_CHANNEL_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            store_msg_id = copied.message_id
            bot.send_message(
                STORE_CHANNEL_ID,
                caption_html,
                parse_mode="HTML",
                reply_to_message_id=store_msg_id
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to store file. Ensure bot is admin in storage channel.\n`{e}`")
        return

    files_db[code] = {
        "owner": uid,
        "store_msg_id": int(store_msg_id),
        "type": ctype,
        "caption": original_caption or "",
        "created_at": created_at,
        "access": {"mode": "public", "limit": None, "viewed_by": []}
    }
    save_files_db()

    if uid in bundle_sessions:
        bundle_sessions[uid].append(code)
        bot.send_message(message.chat.id, f"➕ Added to bundle.\n`{build_share_link(code)}`")
    else:
        bot.send_message(
            message.chat.id,
            f"✅ File stored.\n🔗 Share link:\n`{build_share_link(code)}`\n"
            f"Or share the code: `{code}`"
        )
    # Prompt privacy for both single upload and bundle item
    after_upload_privacy_prompt(message.chat.id, code, "file")

# ==============================
#           REDEEM FLOW
# ==============================
@bot.message_handler(func=lambda msg: msg.content_type == 'text' and not msg.text.startswith('/'), content_types=['text'])
def redeem_code(message):
    user_id = message.from_user.id
    if user_id in banned_users:
        return
    if not has_joined_channel(user_id):
        bot.send_message(
            user_id,
            f"⚠️ **ACTION REQUIRED**\n\nYou must join our channel to redeem codes:\n➡️ {FORCE_JOIN_CHANNEL_LINK}\n\n"
            "Public file hosting is open — you can still upload & share files."
        )
        return

    code = message.text.strip()
    info = codes_db.get(code)
    if not info:
        return bot.send_message(user_id, "❌ **Invalid Code**\nThe code you entered does not exist.")

    # Expiry check
    exp = info.get("expires_at")
    if exp and time.time() > exp:
        return bot.send_message(user_id, "⏳ This code has expired.")

    # Usage check
    max_uses = int(info.get("max_uses", 1))
    used_count = int(info.get("used_count", 0))
    if used_count >= max_uses:
        return bot.send_message(user_id, "❌ Code usage limit reached.")

    # Mark usage
    info["used_count"] = used_count + 1
    save_codes_db()

    acc = info["account"]
    category = info["category"]

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("📸 Send Proof Screenshot", callback_data=f"proof_{code}"))

    bot.send_message(
        user_id,
        f"🎉 **Success! Your {category} Account**:\n\n`{acc}`\n\nEnjoy! Please save your details securely.\n\nYou can also send a proof screenshot below.",
        reply_markup=markup
    )

    text = (
        f"🎉 New Code Redeem!\n"
        f"User: {message.from_user.first_name} (@{message.from_user.username})\n"
        f"Code: {code}\n"
        f"User ID: {user_id}\n"
        f"Type: {category}"
    )
    send_to_data_channel(text)

# ==============================
#      RUN BOT
# ==============================
if __name__ == '__main__':
    print("🔄 Loading data from files...")
    load_data()
    try:
        me = bot.get_me()
        BOT_USERNAME = (me.username or "").strip()
        print(f"🤖 Bot username: @{BOT_USERNAME}")
    except Exception as e:
        print("⚠️ Could not fetch bot username:", e)
    print("🤖 Bot is now running...")
    bot.infinity_polling()