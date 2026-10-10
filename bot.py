# ============================================================
# CELL — bot.py (BẢN 1 FILE DUY NHẤT)  •  COPY TOÀN BỘ Ô NÀY
# ============================================================
"""
Bot Telegram "Kiếm thưởng" — 1 file duy nhất.
Tính năng: vượt link · trồng cây · kênh đối tác · mời bạn bè · rút tiền · kênh thông báo.

Chạy:
    pip install "python-telegram-bot[rate-limiter]" python-dotenv
    export TELEGRAM_BOT_TOKEN="8404419444:AAE..."     # Windows: set TELEGRAM_BOT_TOKEN=...
    python bot.py
Hoặc tạo file .env cạnh bot.py:
    TELEGRAM_BOT_TOKEN=8404419444:AAE...
    ADMIN_IDS=123456789
    ANNOUNCE_CHANNEL=@kenh_cua_ban
"""
import os, sqlite3, time, logging, datetime, asyncio
from contextlib import contextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton, BotCommand, Bot)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (Application, ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, ConversationHandler, filters)

# ============================================================
# 1) CẤU HÌNH  (sửa ở đây hoặc đặt qua biến môi trường / .env)
# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")     # <-- có thể điền token trực tiếp nếu muốn
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
ANNOUNCE_CHANNEL = os.environ.get("ANNOUNCE_CHANNEL", "")

COIN = "Coin"
START_COINS = 0
REFERRAL_BONUS = 500
REFERRAL_PERCENT = 10
TASK_MIN_WAIT = 15
TREE_PLANT_COST = 1000
TREE_WATER_COOLDOWN_H = 24
TREE_STAGES = 5
TREE_HARVEST_REWARD = 6000
MIN_WITHDRAW = 10000
WITHDRAW_METHODS = {"momo": "Ví MoMo", "bank": "Chuyển khoản ngân hàng", "usdt": "USDT (TRC20)"}
DB_PATH = os.environ.get("DB_PATH", "bot.db")

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

# ============================================================
# 2) DỮ LIỆU (SQLite)
# ============================================================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    coins INTEGER NOT NULL DEFAULT 0, total_earned INTEGER NOT NULL DEFAULT 0,
    ref_by INTEGER, ref_count INTEGER NOT NULL DEFAULT 0,
    tree_stage INTEGER NOT NULL DEFAULT -1, tree_last_water TEXT,
    is_banned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, url TEXT NOT NULL,
    reward INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_completions (
    user_id INTEGER NOT NULL, task_id INTEGER NOT NULL, completed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, task_id)
);
CREATE TABLE IF NOT EXISTS partner_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, title TEXT NOT NULL,
    invite_url TEXT NOT NULL, reward INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS channel_joins (
    user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
    rewarded INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, channel_id)
);
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    method TEXT NOT NULL, account TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


@contextmanager
def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db(path=None):
    with connect(path) as con:
        con.executescript(_SCHEMA)


def get_user(user_id, path=None):
    with connect(path) as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_or_create_user(user_id, username, first_name, ref_by=None, path=None):
    with connect(path) as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return dict(row), False
        if ref_by and int(ref_by) != int(user_id):
            if not con.execute("SELECT 1 FROM users WHERE user_id=?", (ref_by,)).fetchone():
                ref_by = None
        else:
            ref_by = None
        con.execute("INSERT INTO users(user_id,username,first_name,coins,ref_by,created_at) "
                    "VALUES(?,?,?,?,?,?)", (user_id, username, first_name, START_COINS, ref_by, _now()))
        if ref_by:
            con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+?, "
                        "ref_count=ref_count+1 WHERE user_id=?", (REFERRAL_BONUS, REFERRAL_BONUS, ref_by))
        return dict(con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()), True


def add_coins(user_id, amount, path=None):
    with connect(path) as con:
        con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+MAX(?,0) "
                    "WHERE user_id=?", (amount, amount, user_id))


def all_user_ids(path=None):
    with connect(path) as con:
        return [r["user_id"] for r in con.execute("SELECT user_id FROM users")]


def count_users(path=None):
    with connect(path) as con:
        return con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def top_inviters(limit=10, path=None):
    with connect(path) as con:
        rows = con.execute("SELECT user_id, first_name, ref_count, coins FROM users "
                           "WHERE ref_count>0 ORDER BY ref_count DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_task(title, url, reward, path=None):
    with connect(path) as con:
        return con.execute("INSERT INTO tasks(title,url,reward,created_at) VALUES(?,?,?,?)",
                           (title, url, reward, _now())).lastrowid


def list_tasks(active_only=True, path=None):
    q = "SELECT * FROM tasks" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
    with connect(path) as con:
        return [dict(r) for r in con.execute(q)]


def get_task(task_id, path=None):
    with connect(path) as con:
        r = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None


def toggle_task(task_id, active=None, path=None):
    with connect(path) as con:
        cur = con.execute("SELECT active FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not cur:
            return None
        new = (1 - cur["active"]) if active is None else int(active)
        con.execute("UPDATE tasks SET active=? WHERE id=?", (new, task_id))
        return new


def task_pending_for(user_id, path=None):
    with connect(path) as con:
        rows = con.execute("SELECT t.* FROM tasks t WHERE t.active=1 AND NOT EXISTS ("
                           " SELECT 1 FROM task_completions c WHERE c.user_id=? AND c.task_id=t.id"
                           ") ORDER BY t.id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def complete_task(user_id, task_id, path=None):
    with connect(path) as con:
        t = con.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not t or con.execute("SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?",
                                (user_id, task_id)).fetchone():
            return -1
        reward = t["reward"]
        con.execute("INSERT INTO task_completions(user_id,task_id,completed_at) VALUES(?,?,?)",
                    (user_id, task_id, _now()))
        con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+? WHERE user_id=?",
                    (reward, reward, user_id))
        u = con.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,)).fetchone()
        if u and u["ref_by"]:
            bonus = reward * REFERRAL_PERCENT // 100
            if bonus > 0:
                con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+? WHERE user_id=?",
                            (bonus, bonus, u["ref_by"]))
        return reward


def plant_tree(user_id, path=None):
    with connect(path) as con:
        u = con.execute("SELECT coins, tree_stage FROM users WHERE user_id=?", (user_id,)).fetchone()
        if u is None:
            return False, "Bạn chưa /start."
        if u["tree_stage"] != -1:
            return False, "Bạn đã có cây rồi, hãy tưới nước cho nó!"
        if u["coins"] < TREE_PLANT_COST:
            return False, f"Cần {TREE_PLANT_COST} {COIN} để trồng cây."
        con.execute("UPDATE users SET coins=coins-?, tree_stage=0, tree_last_water=NULL WHERE user_id=?",
                    (TREE_PLANT_COST, user_id))
        return True, "Đã trồng cây! 🌱"


def water_tree(user_id, path=None):
    with connect(path) as con:
        u = con.execute("SELECT tree_stage, tree_last_water FROM users WHERE user_id=?", (user_id,)).fetchone()
        if u is None or u["tree_stage"] < 0:
            return False, "Bạn chưa có cây. Dùng /tree để trồng.", False
        if u["tree_stage"] >= TREE_STAGES:
            return False, "Cây đã trưởng thành, hãy thu hoạch! 🎉", True
        last = u["tree_last_water"]
        if last:
            elapsed = datetime.datetime.utcnow() - datetime.datetime.fromisoformat(last)
            if elapsed.total_seconds() < TREE_WATER_COOLDOWN_H * 3600:
                remain = TREE_WATER_COOLDOWN_H * 3600 - elapsed.total_seconds()
                return False, f"Vui lòng đợi {int(remain//3600)}h{int((remain%3600)//60)}m nữa mới tưới tiếp.", False
        new_stage = u["tree_stage"] + 1
        con.execute("UPDATE users SET tree_stage=?, tree_last_water=? WHERE user_id=?",
                    (new_stage, _now(), user_id))
        done = new_stage >= TREE_STAGES
        return True, ("Cây đã trưởng thành! Hãy thu hoạch 🎉" if done
                      else f"Cây đã lớn lên (cấp {new_stage}/{TREE_STAGES}) 🌿"), done


def harvest_tree(user_id, path=None):
    with connect(path) as con:
        u = con.execute("SELECT tree_stage FROM users WHERE user_id=?", (user_id,)).fetchone()
        if u is None or u["tree_stage"] < TREE_STAGES:
            return False, "Cây chưa trưởng thành, chưa thể thu hoạch."
        con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+?, "
                    "tree_stage=-1, tree_last_water=NULL WHERE user_id=?",
                    (TREE_HARVEST_REWARD, TREE_HARVEST_REWARD, user_id))
        return True, TREE_HARVEST_REWARD


def add_channel(chat_id, title, invite_url, reward=0, path=None):
    with connect(path) as con:
        return con.execute("INSERT INTO partner_channels(chat_id,title,invite_url,reward) VALUES(?,?,?,?)",
                           (chat_id, title, invite_url, reward)).lastrowid


def list_channels(active_only=True, path=None):
    q = "SELECT * FROM partner_channels" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    with connect(path) as con:
        return [dict(r) for r in con.execute(q)]


def is_channel_rewarded(user_id, channel_id, path=None):
    with connect(path) as con:
        r = con.execute("SELECT rewarded FROM channel_joins WHERE user_id=? AND channel_id=?",
                        (user_id, channel_id)).fetchone()
        return bool(r and r["rewarded"])


def set_channel_join(user_id, channel_id, joined, path=None):
    with connect(path) as con:
        con.execute("INSERT INTO channel_joins(user_id,channel_id,rewarded,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id,channel_id) DO UPDATE SET updated_at=excluded.updated_at",
                    (user_id, channel_id, 1 if joined else 0, _now()))


def claim_channel_reward(user_id, channel_id, path=None):
    with connect(path) as con:
        ch = con.execute("SELECT reward FROM partner_channels WHERE id=?", (channel_id,)).fetchone()
        if not ch:
            return 0
        r = con.execute("SELECT rewarded FROM channel_joins WHERE user_id=? AND channel_id=?",
                        (user_id, channel_id)).fetchone()
        if r and r["rewarded"]:
            return 0
        reward = ch["reward"]
        con.execute("INSERT INTO channel_joins(user_id,channel_id,rewarded,updated_at) VALUES(?,?,1,?) "
                    "ON CONFLICT(user_id,channel_id) DO UPDATE SET rewarded=1, updated_at=excluded.updated_at",
                    (user_id, channel_id, _now()))
        if reward > 0:
            con.execute("UPDATE users SET coins=coins+?, total_earned=total_earned+? WHERE user_id=?",
                        (reward, reward, user_id))
        return reward


def create_withdrawal(user_id, amount, method, account, path=None):
    if amount < MIN_WITHDRAW:
        return False, f"Số {COIN} rút tối thiểu là {MIN_WITHDRAW}."
    with connect(path) as con:
        u = con.execute("SELECT coins FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not u or u["coins"] < amount:
            return False, "Số dư không đủ."
        con.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (amount, user_id))
        return True, con.execute("INSERT INTO withdrawals(user_id,amount,method,account,status,created_at) "
                                 "VALUES(?,?,?,?, 'pending', ?)", (user_id, amount, method, account, _now())).lastrowid


def list_withdrawals(status="pending", path=None):
    q, args = "SELECT * FROM withdrawals", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    with connect(path) as con:
        return [dict(r) for r in con.execute(q + " ORDER BY id DESC", args)]


def set_withdrawal_status(wid, status, path=None):
    with connect(path) as con:
        w = con.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
        if not w or w["status"] != "pending":
            return False
        con.execute("UPDATE withdrawals SET status=?, processed_at=? WHERE id=?", (status, _now(), wid))
        if status == "rejected":
            con.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (w["amount"], w["user_id"]))
        return True


def fmt(n):
    return f"{int(n):,}".replace(",", ".")


# ============================================================
# 3) HANDLERS
# ============================================================
BTN_TASK, BTN_TREE, BTN_CHANNEL, BTN_INVITE, BTN_WITHDRAW, BTN_ACCOUNT = (
    "🔗 Vượt link", "🌱 Trồng cây", "📢 Kênh đối tác", "👥 Mời bạn bè", "💰 Rút tiền", "👤 Tài khoản")
(W_METHOD, W_ACCOUNT) = range(2)


def is_admin(uid):
    return uid in ADMIN_IDS


def main_menu():
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_TASK), KeyboardButton(BTN_TREE)],
                                [KeyboardButton(BTN_CHANNEL), KeyboardButton(BTN_INVITE)],
                                [KeyboardButton(BTN_WITHDRAW), KeyboardButton(BTN_ACCOUNT)]], resize_keyboard=True)


async def cmd_id(update, context):
    await update.message.reply_text(f"🆔 ID của bạn: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_start(update, context):
    user = update.effective_user
    ref_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_by = int(context.args[0][4:])
        except ValueError:
            ref_by = None
    u, created = get_or_create_user(user.id, user.username, user.first_name, ref_by=ref_by)
    if created and ref_by:
        try:
            await context.bot.send_message(ref_by, f"🎉 Có người mới qua link của bạn! +{fmt(REFERRAL_BONUS)} {COIN}")
        except Exception:
            pass
    await update.message.reply_text(f"👋 Xin chào *{user.first_name}*!\n\n💵 Số dư: *{fmt(u['coins'])} {COIN}*\n\n"
                                    "Chọn một mục bên dưới để bắt đầu kiếm thưởng 👇",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def cmd_account(update, context):
    u = get_user(update.effective_user.id)
    if not u:
        return await cmd_start(update, context)
    tree_txt = "❌ Chưa trồng cây" if u["tree_stage"] < 0 else f"🌿 Cấp {u['tree_stage']}/{TREE_STAGES}"
    await update.message.reply_text(f"👤 *Tài khoản*\n\n🆔 `{u['user_id']}`\n💵 Số dư: *{fmt(u['coins'])} {COIN}*\n"
                                    f"📈 Tổng kiếm: {fmt(u['total_earned'])} {COIN}\n👥 Đã mời: {u['ref_count']}\n🌱 Cây: {tree_txt}",
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_tasks(update, context):
    uid = update.effective_user.id
    get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)
    tasks = task_pending_for(uid)
    if not tasks:
        return await update.message.reply_text("🎉 Bạn đã hoàn thành tất cả nhiệm vụ! Quay lại sau nhé.")
    rows = [[InlineKeyboardButton(f"{t['title']} (+{fmt(t['reward'])})", callback_data=f"task_{t['id']}")] for t in tasks[:20]]
    await update.message.reply_text("🔗 *Nhiệm vụ vượt link*\nChọn một nhiệm vụ:",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))


async def cb_task_open(update, context):
    q = update.callback_query
    await q.answer()
    tid = int(q.data.split("_")[1])
    t = get_task(tid)
    if not t:
        return await q.edit_message_text("Nhiệm vụ không tồn tại.")
    context.user_data.setdefault("opened", {})[str(tid)] = time.time()
    kb = [[InlineKeyboardButton("🔗 Mở link", url=t["url"])],
          [InlineKeyboardButton("✅ Tôi đã hoàn thành", callback_data=f"done_{tid}")]]
    await q.edit_message_text(f"📌 *{t['title']}*\n➕ Thưởng: *{fmt(t['reward'])} {COIN}*\n\n"
                              "1) Nhấn “Mở link” và thực hiện.\n2) Quay lại và nhấn “Tôi đã hoàn thành”.",
                              parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def cb_task_done(update, context):
    q = update.callback_query
    tid = int(q.data.split("_")[1])
    opened = context.user_data.get("opened", {}).get(str(tid))
    if not opened or (time.time() - opened) < TASK_MIN_WAIT:
        return await q.answer(f"⏳ Hãy thực hiện link ít nhất {TASK_MIN_WAIT}s rồi thử lại.", show_alert=True)
    reward = complete_task(q.from_user.id, tid)
    await q.answer()
    if reward == -1:
        return await q.edit_message_text("Bạn đã hoàn thành nhiệm vụ này rồi.")
    await q.edit_message_text(f"✅ Hoàn thành! Bạn nhận *+{fmt(reward)} {COIN}*. 🎉", parse_mode=ParseMode.MARKDOWN)


def tree_view(u):
    stage = u["tree_stage"]
    if stage < 0:
        return ("🌱 Bạn chưa có cây. Trồng ngay để bắt đầu nhận thưởng!",
                InlineKeyboardMarkup([[InlineKeyboardButton(f"🌱 Trồng cây ({fmt(TREE_PLANT_COST)} {COIN})", callback_data="tree_plant")]]))
    bar = "🟩" * stage + "⬜" * (TREE_STAGES - stage)
    if stage >= TREE_STAGES:
        return (f"🌳 *Cây đã trưởng thành!*\n{bar}\nThu hoạch để nhận *{fmt(TREE_HARVEST_REWARD)} {COIN}*.",
                InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Thu hoạch", callback_data="tree_harvest")]]))
    return (f"🌿 *Cây của bạn* (cấp {stage}/{TREE_STAGES})\n{bar}\nTưới nước mỗi {TREE_WATER_COOLDOWN_H}h để cây lớn lên.",
            InlineKeyboardMarkup([[InlineKeyboardButton("💧 Tưới nước", callback_data="tree_water")]]))


async def cmd_tree(update, context):
    uid = update.effective_user.id
    get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)
    txt, kb = tree_view(get_user(uid))
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cb_tree(update, context):
    q = update.callback_query
    action = q.data.split("_")[1]
    await q.answer()
    if action == "plant":
        ok, msg = plant_tree(q.from_user.id)
    elif action == "water":
        ok, msg, _ = water_tree(q.from_user.id)
    else:
        ok, res = harvest_tree(q.from_user.id)
        msg = f"🎉 Thu hoạch thành công! +{fmt(res)} {COIN}" if ok else res
    txt, kb = tree_view(get_user(q.from_user.id))
    await q.edit_message_text(f"{msg}\n\n{txt}" if ok else f"⚠️ {msg}\n\n{txt}", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_channels(update, context):
    chans = list_channels()
    if not chans:
        return await update.message.reply_text("Chưa có kênh đối tác nào. Quay lại sau nhé!")
    for ch in chans:
        kb = [[InlineKeyboardButton("📥 Tham gia", url=ch["invite_url"]),
               InlineKeyboardButton("✅ Kiểm tra", callback_data=f"chk_{ch['id']}")]]
        await update.message.reply_text(f"📢 *{ch['title']}*\n➕ Thưởng: {fmt(ch['reward'])} {COIN}",
                                        parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def cb_channel_check(update, context):
    q = update.callback_query
    cid = int(q.data.split("_")[1])
    ch = next((c for c in list_channels() if c["id"] == cid), None)
    if not ch:
        return await q.answer("Kênh không tồn tại.", show_alert=True)
    try:
        member = await context.bot.get_chat_member(ch["chat_id"], q.from_user.id)
        joined = member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                   ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED)
    except Exception as e:
        log.warning("get_chat_member failed: %s", e)
        return await q.answer("⚠️ Không kiểm tra được. Hãy chắc bot là quản trị viên của kênh đó.", show_alert=True)
    if not joined:
        return await q.answer("❌ Bạn chưa tham gia kênh này.", show_alert=True)
    reward = claim_channel_reward(q.from_user.id, cid)
    await q.answer("✅ Đã nhận thưởng!" if reward else "Bạn đã nhận thưởng kênh này rồi.", show_alert=True)
    if reward:
        try:
            await q.edit_message_text(f"📢 *{ch['title']}*\n✅ Đã tham gia, +{fmt(reward)} {COIN} 🎉", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


async def cmd_invite(update, context):
    uid = update.effective_user.id
    u = get_or_create_user(uid, update.effective_user.username, update.effective_user.first_name)[0]
    link = f"https://t.me/{context.bot.username}?start=ref_{uid}"
    share = f"https://t.me/share/url?url={link}&text=Tham%20gia%20bot%20kiếm%20thưởng!"
    await update.message.reply_text(f"👥 *Mời bạn bè*\n\n🔗 Link của bạn:\n`{link}`\n\n"
                                    f"🎁 Mỗi người bạn mời: *+{fmt(REFERRAL_BONUS)} {COIN}*\n"
                                    f"💸 Hoa hồng: *{REFERRAL_PERCENT}%* mỗi lần bạn bè kiếm thưởng\n\n"
                                    f"👥 Đã mời: *{u['ref_count']}* người", parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Chia sẻ link", url=share)]]))


async def w_start(update, context):
    u = get_user(update.effective_user.id)
    if not u or u["coins"] < MIN_WITHDRAW:
        bal = fmt(u["coins"]) if u else "0"
        await update.message.reply_text(f"⚠️ Số dư: *{bal} {COIN}*.\nCần tối thiểu *{fmt(MIN_WITHDRAW)} {COIN}* để rút.",
                                        parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    rows = [[InlineKeyboardButton(label, callback_data=f"wm_{code}")] for code, label in WITHDRAW_METHODS.items()]
    await update.message.reply_text("💰 Chọn phương thức rút tiền:", reply_markup=InlineKeyboardMarkup(rows))
    return W_METHOD


async def w_method(update, context):
    q = update.callback_query
    await q.answer()
    code = q.data[3:]
    if code not in WITHDRAW_METHODS:
        return W_METHOD
    context.user_data["w_method"] = code
    await q.edit_message_text(f"Bạn chọn: *{WITHDRAW_METHODS[code]}*.\nNhập số {COIN} muốn rút (tối thiểu {fmt(MIN_WITHDRAW)}):",
                              parse_mode=ParseMode.MARKDOWN)
    return W_ACCOUNT


async def w_account(update, context):
    text = (update.message.text or "").strip().replace(".", "").replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("⚠️ Vui lòng nhập số nguyên. Thử lại:")
        return W_ACCOUNT
    context.user_data["w_amount"] = int(text)
    method = context.user_data.get("w_method", "bank")
    await update.message.reply_text(f"Bạn muốn rút *{fmt(int(text))} {COIN}* qua *{WITHDRAW_METHODS[method]}*.\n\n"
                                    "Nhập *thông tin nhận tiền* (số MoMo / số TK + tên / ví USDT):",
                                    parse_mode=ParseMode.MARKDOWN)
    return W_ACCOUNT + 1


async def w_details(update, context):
    account = (update.message.text or "").strip()
    amount = context.user_data.get("w_amount", 0)
    method = context.user_data.get("w_method", "bank")
    uid = update.effective_user.id
    ok, res = create_withdrawal(uid, amount, WITHDRAW_METHODS.get(method, method), account)
    if not ok:
        await update.message.reply_text(f"⚠️ {res}", reply_markup=main_menu())
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Đã gửi yêu cầu rút *#WH{res}*: {fmt(amount)} {COIN}.\n⏳ Chờ admin duyệt.",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, f"💰 *Yêu cầu rút mới* #{res}\nUser: `{uid}`\nSố: {fmt(amount)} {COIN}\n"
                                                f"PT: {WITHDRAW_METHODS.get(method, method)}\nTK: `{account}`\n\n"
                                                f"Duyệt: /approve {res} | Từ chối: /reject {res}", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
    return ConversationHandler.END


async def w_cancel(update, context):
    await update.message.reply_text("❌ Đã hủy.", reply_markup=main_menu())
    return ConversationHandler.END


def admin_only(func):
    async def wrapper(update, context):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Bạn không phải admin.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_help(update, context):
    await update.message.reply_text(
        "🛠 *Bảng điều khiển admin*\n/stats — thống kê\n/addtask `<thưởng>|<tiêu đề>|<url>`\n"
        "/tasks — danh sách nhiệm vụ\n/deltask `<id>` — bật/tắt nhiệm vụ\n"
        "/addchannel `<@chat|id>|<tên>|<invite_url>|<thưởng>`\n/withdrawals — yêu cầu rút đang chờ\n"
        "/approve `<id>` | /reject `<id>`\n/broadcast `<nội dung>` — gửi tới mọi người\n"
        "/post `<nội dung>` — đăng lên kênh thông báo", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_stats(update, context):
    await update.message.reply_text(f"📊 *Thống kê*\n👥 Người dùng: {count_users()}\n🔗 Nhiệm vụ: {len(list_tasks())}\n"
                                    f"📢 Kênh đối tác: {len(list_channels())}\n💰 Rút chờ duyệt: {len(list_withdrawals('pending'))}",
                                    parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_addtask(update, context):
    parts = [p.strip() for p in update.message.text.partition(" ")[2].split("|")]
    if len(parts) != 3 or not parts[0].isdigit():
        return await update.message.reply_text("Cú pháp: /addtask <thưởng>|<tiêu đề>|<url>")
    tid = add_task(parts[1], parts[2], int(parts[0]))
    await update.message.reply_text(f"✅ Đã thêm nhiệm vụ #{tid}: {parts[1]} (+{fmt(int(parts[0]))} {COIN})")


@admin_only
async def admin_tasks(update, context):
    tasks = list_tasks(active_only=False)
    await update.message.reply_text("🔗 *Nhiệm vụ*\n" + ("\n".join(
        f"#{t['id']} [{'ON' if t['active'] else 'OFF'}] {t['title']} (+{fmt(t['reward'])})" for t in tasks) or "Chưa có."),
        parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_deltask(update, context):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        return await update.message.reply_text("Cú pháp: /deltask <id>")
    new = toggle_task(int(arg))
    await update.message.reply_text("Không tìm thấy." if new is None else f"✅ Nhiệm vụ #{arg} → {'ON' if new else 'OFF'}")


@admin_only
async def admin_addchannel(update, context):
    parts = [p.strip() for p in update.message.text.partition(" ")[2].split("|")]
    if len(parts) != 4 or not parts[3].isdigit():
        return await update.message.reply_text("Cú pháp: /addchannel <@chat|id>|<tên>|<invite_url>|<thưởng>")
    cid = add_channel(parts[0], parts[1], parts[2], int(parts[3]))
    await update.message.reply_text(f"✅ Đã thêm kênh #{cid}: {parts[1]}")


@admin_only
async def admin_withdrawals(update, context):
    ws = list_withdrawals("pending")
    if not ws:
        return await update.message.reply_text("✅ Không có yêu cầu rút nào đang chờ.")
    txt = "\n".join(f"#{w['id']} | user `{w['user_id']}` | {fmt(w['amount'])} {COIN} | {w['method']} | {w['account']}" for w in ws)
    await update.message.reply_text(f"💰 *Yêu cầu rút đang chờ*\n{txt}\n\n/approve <id> | /reject <id>", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_approve(update, context):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit() or not set_withdrawal_status(int(arg), "approved"):
        return await update.message.reply_text("Không hợp lệ hoặc đã xử lý.")
    w = [x for x in list_withdrawals(None) if x["id"] == int(arg)][0]
    await update.message.reply_text(f"✅ Đã duyệt #{arg}.")
    try:
        await context.bot.send_message(w["user_id"], f"✅ Yêu cầu rút *#{arg}* ({fmt(w['amount'])} {COIN}) đã được duyệt!")
    except Exception:
        pass


@admin_only
async def admin_reject(update, context):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit() or not set_withdrawal_status(int(arg), "rejected"):
        return await update.message.reply_text("Không hợp lệ hoặc đã xử lý.")
    w = [x for x in list_withdrawals(None) if x["id"] == int(arg)][0]
    await update.message.reply_text(f"❌ Đã từ chối #{arg}, coin đã hoàn cho người dùng.")
    try:
        await context.bot.send_message(w["user_id"], f"❌ Yêu cầu rút *#{arg}* bị từ chối. Coin đã được hoàn lại.")
    except Exception:
        pass


@admin_only
async def admin_broadcast(update, context):
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        return await update.message.reply_text("Cú pháp: /broadcast <nội dung>")
    sent = 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"📣 Đã gửi tới {sent} người dùng.")


@admin_only
async def admin_post(update, context):
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        return await update.message.reply_text("Cú pháp: /post <nội dung>")
    if not ANNOUNCE_CHANNEL:
        return await update.message.reply_text("Chưa cấu hình ANNOUNCE_CHANNEL.")
    await context.bot.send_message(ANNOUNCE_CHANNEL, text)
    await update.message.reply_text("📣 Đã đăng lên kênh thông báo.")


async def on_error(update, context):
    log.exception("Handler error", exc_info=context.error)


async def post_init(app):
    await app.bot.set_my_commands([BotCommand("start", "Bắt đầu / menu"), BotCommand("invite", "Mời bạn bè"),
                                   BotCommand("tree", "Trồng cây"), BotCommand("id", "Lấy ID Telegram"),
                                   BotCommand("admin", "Bảng điều khiển (admin)")])


def build_application(token=None):
    app = ApplicationBuilder().token(token or BOT_TOKEN).post_init(post_init).build()
    withdraw_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_WITHDRAW}$"), w_start)],
        states={W_METHOD: [CallbackQueryHandler(w_method, pattern=r"^wm_")],
                W_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_account)],
                W_ACCOUNT + 1: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_details)]},
        fallbacks=[CommandHandler("cancel", w_cancel)])
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("tree", cmd_tree))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(withdraw_conv)
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_TASK}$"), cmd_tasks))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_TREE}$"), cmd_tree))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CHANNEL}$"), cmd_channels))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_INVITE}$"), cmd_invite))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_ACCOUNT}$"), cmd_account))
    app.add_handler(CallbackQueryHandler(cb_task_open, pattern=r"^task_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_task_done, pattern=r"^done_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_tree, pattern=r"^tree_"))
    app.add_handler(CallbackQueryHandler(cb_channel_check, pattern=r"^chk_\d+$"))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("addtask", admin_addtask))
    app.add_handler(CommandHandler("tasks", admin_tasks))
    app.add_handler(CommandHandler("deltask", admin_deltask))
    app.add_handler(CommandHandler("addchannel", admin_addchannel))
    app.add_handler(CommandHandler("withdrawals", admin_withdrawals))
    app.add_handler(CommandHandler("approve", admin_approve))
    app.add_handler(CommandHandler("reject", admin_reject))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("post", admin_post))
    app.add_error_handler(on_error)
    return app


# ============================================================
# 4) CHẠY BOT
# ============================================================
def main():
    init_db()
    if not BOT_TOKEN:
        raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN. Đặt biến môi trường (hoặc .env) rồi chạy lại.")
    if not ADMIN_IDS:
        print("⚠️  Chưa cấu hình ADMIN_IDS — sẽ không ai dùng được lệnh admin.")
    app = build_application()
    print("🤖 Bot đang chạy... (Ctrl+C để dừng)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def check_token(token=None):
    token = token or BOT_TOKEN
    if not token:
        print("ℹ️  Chưa có token.")
        return None
    me = await Bot(token).get_me()
    print(f"✅ Token hợp lệ! Bot: @{me.username} — {me.full_name} (id={me.id})")
    return me


def _is_notebook():
    try:
        get_ipython  # noqa: F821
        return True
    except NameError:
        return False


if __name__ == "__main__" and not _is_notebook():
    main()
