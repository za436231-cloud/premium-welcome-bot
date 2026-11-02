"""
ESCROW BOT  -  python-telegram-bot 21.6 + SQLite (persistent)

Commands:
  /fee <amount>            - sab ke liye, fee calculate
  /add <amount> [exchange] - admin, form par reply karke deal start
  /close [amount]          - admin, deal complete
  /hold                    - admin, deal ko hold par daalo
  /holdlist                - admin, hold wali deals
  /release [amount]        - admin, hold wali deal release (complete)
  /refund [amount]         - admin, buyer ko refund
  /report                  - admin, nightly report abhi dekho
  /setadmin                - admin, jis group me likho wo ADMIN GROUP ban jata hai
  /start                   - dashboard: Total Deal | My Deal Info | My Deal Hold

Data safety: sab data SQLite file me hai (DB_PATH). Railway par Volume /data par mount karo,
tab bot band/restart/redeploy hone par bhi data zero nahi hoga.
"""
import glob
import html
import logging
import os
import sqlite3
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

# ───────────────────────── CONFIG ─────────────────────────
BOT_TOKEN = os.environ["9094961559:AAFOGXKBekFwW-5ITjN0_axJIXTbRBM8sHA"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "7054168366").split(",") if x.strip()}
DB_PATH = os.getenv("DB_PATH", "/data/escrow.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))
TRADE_PREFIX = os.getenv("TRADE_PREFIX", "DL-ZENOX-")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "0"))      # raat 12:00 IST
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0"))
SEND_BACKUP_TO_TELEGRAM = os.getenv("SEND_BACKUP", "1") == "1"
IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("escrow")
HTML = ParseMode.HTML

STATUS = {"active": "🟢 ACTIVE", "hold": "🔒 HOLD", "closed": "✅ CLOSED", "refunded": "↩️ REFUNDED"}
NOT_FOUND = "❌ Deal nahi mila. Form ya bot ke deal-message par <b>reply</b> karke command do."


# ───────────────────────── DATABASE ─────────────────────────
def conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")      # har write disk par pakka
    return c


def run(sql, args=()):
    c = conn()
    try:
        with c:
            return c.execute(sql, args).lastrowid
    finally:
        c.close()


def get(sql, args=()):
    c = conn()
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


def rows(sql, args=()):
    c = conn()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def init_db():
    run("""CREATE TABLE IF NOT EXISTS deals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT,
        chat_id INTEGER, chat_title TEXT,
        form_msg_id INTEGER, bot_msg_id INTEGER,
        user_id INTEGER, user_name TEXT,
        escrower_id INTEGER, escrower_name TEXT,
        amount REAL, fee REAL, net REAL,
        status TEXT,
        released REAL DEFAULT 0, refunded REAL DEFAULT 0,
        created_at INTEGER, hold_at INTEGER, closed_at INTEGER)""")
    run("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)")
    run("CREATE INDEX IF NOT EXISTS ix_deals_status ON deals(status)")
    run("CREATE INDEX IF NOT EXISTS ix_deals_escrower ON deals(escrower_id, status)")


def set_setting(k, v):
    run("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def get_setting(k):
    r = get("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else None


def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"escrow-{datetime.now(IST):%Y%m%d}.db")
    src, dst = conn(), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "escrow-*.db")))[:-7]:
        os.remove(old)
    return path


# ───────────────────────── HELPERS ─────────────────────────
def money(x):
    return f"₹{float(x or 0):,.2f}"


def parse_amt(s):
    try:
        v = float(str(s).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None
    return round(v, 2) if v > 0 else None


HOLD_FEE_PCT = 5.0   # hold deals ki fee 5%


def fee_calc(a, hold=False):
    """returns (fee, rate_label)"""
    if hold:
        return round(a * HOLD_FEE_PCT / 100, 2), f"{HOLD_FEE_PCT:g}% (HOLD)"
    if a <= 500:
        return 10.0, "₹10 flat"
    elif a <= 1000:
        return 15.0, "₹15 flat"
    elif a <= 5000:
        pct = 2
    elif a <= 10000:
        pct = 1.5
    else:
        pct = 1
    return round(a * pct / 100, 2), f"{pct:g}%"


def ago(ts):
    secs = max(0, int(time.time() - (ts or time.time())))
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def when(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %I:%M %p")


def mention(uid, name):
    return f'<a href="tg://user?id={uid}">{html.escape(name or "User")}</a>'


def tag_name(user):
    return f"@{user.username}" if user.username else user.full_name


async def send_long(bot, chat_id, text):
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 3800:
            await bot.send_message(chat_id, chunk, parse_mode=HTML)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await bot.send_message(chat_id, chunk, parse_mode=HTML)


async def admin_only(update: Update):
    u = update.effective_user
    if u and u.id in ADMIN_IDS:
        return True
    await update.effective_message.reply_text("⛔ Ye command sirf admin ke liye hai.")
    return False


def target(update: Update):
    """Reply se deal dhundo; reply na ho to admin ki akeli open deal."""
    m = update.effective_message
    if m.reply_to_message:
        rid = m.reply_to_message.message_id
        return get("""SELECT * FROM deals WHERE chat_id=? AND (form_msg_id=? OR bot_msg_id=?)
                      ORDER BY id DESC LIMIT 1""", (m.chat_id, rid, rid))
    r = rows("""SELECT * FROM deals WHERE chat_id=? AND escrower_id=?
                AND status IN ('active','hold')""", (m.chat_id, update.effective_user.id))
    return r[0] if len(r) == 1 else None


def hold_totals(chat_id=None):
    if chat_id is None:
        r = get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) a FROM deals WHERE status='hold'")
    else:
        r = get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) a FROM deals WHERE status='hold' AND chat_id=?",
                (chat_id,))
    return r["n"], r["a"]


# ───────────────────────── /fee ─────────────────────────
async def cmd_fee(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not ctx.args or parse_amt(ctx.args[0]) is None:
        return await m.reply_text("Use: /fee 1000")
    a = parse_amt(ctx.args[0])
    hd = any(x.lower() == "hold" for x in ctx.args[1:])
    fee, rate = fee_calc(a, hd)
    await m.reply_text(
        f"💰 <b>FEE CALCULATE</b>\n\n📌 Deal: {money(a)}\n📊 Rate: {rate}\n"
        f"💸 Fee: {money(fee)}\n✅ Total: {money(a + fee)}", parse_mode=HTML)


# ───────────────────────── /add ─────────────────────────
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m, u = update.effective_message, update.effective_user
    form = m.reply_to_message
    if not form or not ctx.args or parse_amt(ctx.args[0]) is None:
        return await m.reply_text("Use: form par reply karke <code>/add 400</code>", parse_mode=HTML)
    if not form.from_user or form.from_user.is_bot:
        return await m.reply_text("❌ Member ke form message par reply karo.")
    amt = parse_amt(ctx.args[0])

    busy = get("SELECT trade_id FROM deals WHERE escrower_id=? AND status IN ('active','hold') LIMIT 1", (u.id,))
    if busy:
        return await m.reply_text(
            f"⛔ Aapki deal <b>{busy['trade_id']}</b> abhi chal rahi hai.\n"
            f"Pehle use /close, /release ya /refund karo, phir nayi deal lo.", parse_mode=HTML)
    dup = get("SELECT trade_id FROM deals WHERE chat_id=? AND form_msg_id=? AND status IN ('active','hold')",
              (m.chat_id, form.message_id))
    if dup:
        return await m.reply_text(f"⚠️ Is form par deal pehle se chal rahi hai: {dup['trade_id']}")

    fee, rate = fee_calc(amt)
    net = round(amt - fee, 2)
    fu = form.from_user

    c = conn()
    try:
        with c:
            cur = c.execute("""INSERT INTO deals(chat_id,chat_title,form_msg_id,user_id,user_name,
                escrower_id,escrower_name,amount,fee,net,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (m.chat_id, m.chat.title or "Private", form.message_id, fu.id, fu.full_name,
                             u.id, u.full_name, amt, fee, net, "active", int(time.time())))
            did = cur.lastrowid
            trade = f"{TRADE_PREFIX}{did}"
            c.execute("UPDATE deals SET trade_id=? WHERE id=?", (trade, did))
    finally:
        c.close()

    sent = await form.reply_text(
        f"🧾 <b>DEAL ADDED</b>\n\n"
        f"💰 Deal Amount: {money(amt)}\n"
        f"📊 Fee ({rate}): {money(fee)}\n"
        f"📤 Net Release: {money(net)}\n"
        f"🆔 Trade ID: <code>{trade}</code>\n\n"
        f"👤 Member: {mention(fu.id, fu.full_name)}\n"
        f"🛡 Escrowed By: {mention(u.id, u.full_name)}", parse_mode=HTML)
    run("UPDATE deals SET bot_msg_id=? WHERE id=?", (sent.message_id, did))


# ───────────────────────── /close ─────────────────────────
async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m, u = update.effective_message, update.effective_user
    d = target(update)
    if not d:
        return await m.reply_text(NOT_FOUND, parse_mode=HTML)
    if d["status"] == "hold":
        return await m.reply_text("🔒 Ye deal HOLD par hai. Pehle /release karo.")
    if d["status"] != "active":
        return await m.reply_text(f"ℹ️ Ye deal pehle hi {d['status']} ho chuki hai.")
    rel = d["net"]
    if ctx.args:
        rel = parse_amt(ctx.args[0])
        if rel is None:
            return await m.reply_text("Use: /close 360")
    run("UPDATE deals SET status='closed', released=?, closed_at=? WHERE id=?",
        (rel, int(time.time()), d["id"]))
    await m.reply_text(
        f"✅ <b>Deal Completed</b>\n\n"
        f"🆔 Trade ID: <code>{d['trade_id']}</code>\n"
        f"💸 Released: {money(rel)}\n"
        f"🛡 Escrowed By: {mention(u.id, u.full_name)}\n\n"
        f"~ {mention(d['user_id'], d['user_name'])} is requested to drop the vouch before leaving👇🏻\n\n"
        f"<code>I Vouch {html.escape(tag_name(u))} for {money(rel)} smooth escrow deal</code>",
        parse_mode=HTML)


# ───────────────────────── /hold  /holdlist  /release ─────────────────────────
async def cmd_hold(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m, u = update.effective_message, update.effective_user
    d = target(update)
    if not d:
        return await m.reply_text(NOT_FOUND, parse_mode=HTML)
    if d["status"] != "active":
        return await m.reply_text(f"ℹ️ Deal ka status abhi {d['status']} hai, hold nahi ho sakti.")
    hfee, hrate = fee_calc(d["amount"], hold=True)
    hnet = round(d["amount"] - hfee, 2)
    run("UPDATE deals SET status='hold', hold_at=?, fee=?, net=? WHERE id=?",
        (int(time.time()), hfee, hnet, d["id"]))
    n, total = hold_totals(m.chat_id)
    await m.reply_text(
        f"🔒 <b>Deal added to HOLD list</b>\n\n"
        f"🆔 Trade ID: <code>{d['trade_id']}</code>\n"
        f"💰 Amount: {money(d['amount'])}\n"
        f"📊 Hold Fee ({hrate}): {money(hfee)}\n"
        f"📤 Net Release: {money(hnet)}\n"
        f"🛡 Held By: {mention(u.id, u.full_name)}\n\n"
        f"📦 Total on Hold: {n} deal | {money(total)}", parse_mode=HTML)


async def cmd_holdlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m = update.effective_message
    r = rows("SELECT * FROM deals WHERE status='hold' AND chat_id=? ORDER BY hold_at", (m.chat_id,))
    if not r:
        return await m.reply_text("✅ Is group me koi deal hold par nahi hai.")
    out = ["🔒 <b>HOLD LIST</b>\n"]
    for d in r:
        out.append(
            f"🆔 <code>{d['trade_id']}</code> | {money(d['amount'])}\n"
            f"👤 Member: {mention(d['user_id'], d['user_name'])}\n"
            f"🛡 Escrower: {html.escape(d['escrower_name'] or '')}\n"
            f"🕐 Hold: {when(d['hold_at'])}  (⏱ {ago(d['hold_at'])})\n")
    n, total = hold_totals(m.chat_id)
    out.append(f"📦 Total on Hold: {n} deal | {money(total)}")
    await send_long(ctx.bot, m.chat_id, "\n".join(out))


async def cmd_release(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m, u = update.effective_message, update.effective_user
    d = target(update)
    if not d:
        return await m.reply_text(NOT_FOUND, parse_mode=HTML)
    if d["status"] != "hold":
        return await m.reply_text("ℹ️ Release sirf HOLD wali deal ka hota hai. Active deal ke liye /close use karo.")
    rel = d["net"]
    if ctx.args:
        rel = parse_amt(ctx.args[0])
        if rel is None:
            return await m.reply_text("Use: /release 360")
    run("UPDATE deals SET status='closed', released=?, closed_at=? WHERE id=?",
        (rel, int(time.time()), d["id"]))
    await m.reply_text(
        f"🔓 <b>Deal Released</b>\n\n"
        f"🆔 Trade ID: <code>{d['trade_id']}</code>\n"
        f"💸 Released: {money(rel)}\n"
        f"⏱ Hold Time: {ago(d['hold_at'])}\n"
        f"🛡 Released By: {mention(u.id, u.full_name)}\n\n"
        f"~ {mention(d['user_id'], d['user_name'])} is requested to drop the vouch before leaving👇🏻\n\n"
        f"<code>I Vouch {html.escape(tag_name(u))} for {money(rel)} smooth escrow deal</code>",
        parse_mode=HTML)


# ───────────────────────── /refund ─────────────────────────
async def cmd_refund(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    m, u = update.effective_message, update.effective_user
    d = target(update)
    if not d:
        return await m.reply_text(NOT_FOUND, parse_mode=HTML)
    if d["status"] not in ("active", "hold"):
        return await m.reply_text(f"ℹ️ Ye deal pehle hi {d['status']} ho chuki hai.")
    ref = d["amount"]
    if ctx.args:
        ref = parse_amt(ctx.args[0])
        if ref is None:
            return await m.reply_text("Use: /refund 400")
    run("UPDATE deals SET status='refunded', refunded=?, fee=0, net=amount, closed_at=? WHERE id=?",
        (ref, int(time.time()), d["id"]))
    await m.reply_text(
        f"↩️ <b>Deal Refunded</b>\n\n"
        f"🆔 Trade ID: <code>{d['trade_id']}</code>\n"
        f"💸 Refund Amount: {money(ref)}\n"
        f"📊 Fee: ₹0.00 (refund deals me koi fee nahi)\n"
        f"👤 Member: {mention(d['user_id'], d['user_name'])}\n"
        f"🛡 Refunded By: {mention(u.id, u.full_name)}", parse_mode=HTML)


# ───────────────────────── REPORT (nightly) ─────────────────────────
def build_report():
    now = time.time()
    since = now - 86400
    opn = rows("SELECT * FROM deals WHERE status IN ('active','hold') ORDER BY created_at")
    out = [f"📊 <b>NIGHTLY ESCROW REPORT</b>\n🗓 {datetime.now(IST):%d %b %Y, %I:%M %p}\n"]

    act = [d for d in opn if d["status"] == "active"]
    hld = [d for d in opn if d["status"] == "hold"]
    out.append(f"🟢 Active: {len(act)} deal | {money(sum(d['amount'] for d in act))}")
    out.append(f"🔒 Hold: {len(hld)} deal | {money(sum(d['amount'] for d in hld))}\n")

    if opn:
        out.append("━━━━━━━━━━━━━━━")
        out.append("<b>OPEN DEALS</b>\n")
        for d in opn:
            line = (f"🆔 <code>{d['trade_id']}</code> | {html.escape(d['chat_title'] or '')}\n"
                    f"💰 {money(d['amount'])} | {STATUS[d['status']]}\n"
                    f"🛡 Escrower: {html.escape(d['escrower_name'] or '')}\n"
                    f"⏱ Deal ko hue: {ago(d['created_at'])}")
            if d["status"] == "hold":
                line += f"\n🔒 Hold ko hue: {ago(d['hold_at'])}"
            out.append(line + "\n")
    else:
        out.append("✅ Koi open deal nahi hai.\n")

    n = get("SELECT COUNT(*) c FROM deals WHERE created_at>=?", (since,))["c"]
    cl = get("SELECT COUNT(*) c, COALESCE(SUM(released),0) s FROM deals WHERE status='closed' AND closed_at>=?", (since,))
    rf = get("SELECT COUNT(*) c, COALESCE(SUM(refunded),0) s FROM deals WHERE status='refunded' AND closed_at>=?", (since,))
    out.append("━━━━━━━━━━━━━━━")
    out.append("<b>LAST 24 HOURS</b>")
    out.append(f"🆕 New: {n}\n✅ Closed: {cl['c']} | {money(cl['s'])}\n↩️ Refund: {rf['c']} | {money(rf['s'])}\n")

    t = get("""SELECT COUNT(*) n, COALESCE(SUM(amount),0) a,
               COALESCE(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END),0) c,
               COALESCE(SUM(CASE WHEN status='closed' THEN released ELSE 0 END),0) rel,
               COALESCE(SUM(CASE WHEN status='refunded' THEN 1 ELSE 0 END),0) r
               FROM deals""")
    out.append("━━━━━━━━━━━━━━━")
    out.append("<b>ALL TIME</b>")
    out.append(f"📦 Total Deals: {t['n']} | {money(t['a'])}\n✅ Closed: {t['c']} | Released {money(t['rel'])}\n↩️ Refunded: {t['r']}")
    return "\n".join(out)


async def nightly(ctx: ContextTypes.DEFAULT_TYPE):
    gid = get_setting("admin_group")
    if not gid:
        log.warning("Admin group set nahi hai - /setadmin chalao")
        return
    try:
        await send_long(ctx.bot, int(gid), build_report())
    except Exception:
        log.exception("report send failed")
    try:
        path = make_backup()
        if SEND_BACKUP_TO_TELEGRAM:
            with open(path, "rb") as f:
                await ctx.bot.send_document(int(gid), f, caption="💾 Daily database backup")
    except Exception:
        log.exception("backup failed")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await send_long(ctx.bot, update.effective_chat.id, build_report())


async def cmd_setadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    set_setting("admin_group", update.effective_chat.id)
    await update.effective_message.reply_text("✅ Ye group ADMIN GROUP set ho gaya. Roz raat 12 baje report yahin aayegi.")


# ───────────────────────── /start dashboard ─────────────────────────
def kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Total Deal", callback_data="total")],
        [InlineKeyboardButton("👤 My Deal Info", callback_data="mine"),
         InlineKeyboardButton("🔒 My Deal Hold", callback_data="myhold")],
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.effective_message.reply_text(
        f"⭐️ <b>Welcome {html.escape(u.full_name)}!</b>\n\n"
        f"❤️ Escrow Bot\n🍑 This is Your Personal Dashboard:\n\nSelect the option below ⚡️",
        parse_mode=HTML, reply_markup=kb())


def _stats(where, args):
    return get(f"""SELECT COUNT(*) n, COALESCE(SUM(amount),0) a,
        COALESCE(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END),0) cn,
        COALESCE(SUM(CASE WHEN status='closed' THEN released ELSE 0 END),0) rel,
        COALESCE(SUM(CASE WHEN status='refunded' THEN 1 ELSE 0 END),0) rn,
        COALESCE(SUM(CASE WHEN status='refunded' THEN refunded ELSE 0 END),0) ref,
        COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) an,
        COALESCE(SUM(CASE WHEN status='active' THEN amount ELSE 0 END),0) aa,
        COALESCE(SUM(CASE WHEN status='hold' THEN 1 ELSE 0 END),0) hn,
        COALESCE(SUM(CASE WHEN status='hold' THEN amount ELSE 0 END),0) ha
        FROM deals {where}""", args)


def _block(s):
    return (f"📦 Total Deals: {s['n']} | {money(s['a'])}\n"
            f"✅ Closed: {s['cn']} | Released {money(s['rel'])}\n"
            f"↩️ Refunded: {s['rn']} | {money(s['ref'])}\n"
            f"🟢 Active: {s['an']} | {money(s['aa'])}\n"
            f"🔒 Hold: {s['hn']} | {money(s['ha'])}")


def txt_total(chat_id, private):
    if private:
        s = _stats("", ())
        head = "📊 <b>TOTAL DEAL (All Groups)</b>"
    else:
        s = _stats("WHERE chat_id=?", (chat_id,))
        head = "📊 <b>TOTAL DEAL (This Group)</b>"
        first = get("SELECT MIN(created_at) t FROM deals WHERE chat_id=?", (chat_id,))
        if first and first["t"]:
            head += f"\n🕐 Pehli deal ko: {ago(first['t'])} ho gaye"
    return f"{head}\n\n{_block(s)}"


def txt_mine(uid, chat_id, private):
    cf = "" if private else " AND chat_id=?"
    ca = () if private else (chat_id,)
    esc = _stats(f"WHERE escrower_id=?{cf}", (uid, *ca))
    mem = _stats(f"WHERE user_id=?{cf}", (uid, *ca))
    last = rows(f"""SELECT * FROM deals WHERE (user_id=? OR escrower_id=?){cf}
                    ORDER BY id DESC LIMIT 5""", (uid, uid, *ca))
    out = ["👤 <b>MY DEAL INFO</b>\n"]
    out.append("🛡 <b>Maine escrow kiye</b>\n" + _block(esc) + "\n")
    out.append("🙋 <b>Mere forms / deals</b>\n" + _block(mem))
    if last:
        out.append("\n🕘 <b>Last deals</b>")
        for d in last:
            out.append(f"• <code>{d['trade_id']}</code> | {money(d['amount'])} | {STATUS[d['status']]} | {ago(d['created_at'])} pehle")
    return "\n".join(out)


def txt_myhold(uid, chat_id, private):
    cf = "" if private else " AND chat_id=?"
    ca = () if private else (chat_id,)
    r = rows(f"""SELECT * FROM deals WHERE status='hold' AND (user_id=? OR escrower_id=?){cf}
                 ORDER BY hold_at""", (uid, uid, *ca))
    if not r:
        return "🔒 <b>MY DEAL HOLD</b>\n\n✅ Aapki koi deal hold par nahi hai."
    out = ["🔒 <b>MY DEAL HOLD</b>\n"]
    for d in r:
        out.append(f"🆔 <code>{d['trade_id']}</code> | {money(d['amount'])}\n"
                   f"🛡 Escrower: {html.escape(d['escrower_name'] or '')}\n"
                   f"🕐 Hold: {when(d['hold_at'])} (⏱ {ago(d['hold_at'])})\n")
    out.append(f"📦 Total Hold: {len(r)} deal | {money(sum(d['amount'] for d in r))}")
    return "\n".join(out)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat = q.message.chat
    private = chat.type == "private"
    u = q.from_user
    if q.data == "total":
        body = txt_total(chat.id, private)
    elif q.data == "mine":
        body = txt_mine(u.id, chat.id, private)
    else:
        body = txt_myhold(u.id, chat.id, private)
    await q.message.reply_text(f"{mention(u.id, u.full_name)}\n\n{body}", parse_mode=HTML)


# ───────────────────────── MAIN ─────────────────────────
async def post_init(app: Application):
    init_db()
    log.info("DB path: %s", DB_PATH)
    if not DB_PATH.startswith("/data"):
        log.warning("DB_PATH /data volume par nahi hai - redeploy par data ud sakta hai!")


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for name, fn in [("start", cmd_start), ("fee", cmd_fee), ("add", cmd_add), ("close", cmd_close),
                     ("hold", cmd_hold), ("holdlist", cmd_holdlist), ("release", cmd_release),
                     ("refund", cmd_refund), ("report", cmd_report), ("setadmin", cmd_setadmin)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button, pattern="^(total|mine|myhold)$"))
    app.job_queue.run_daily(nightly, time=dtime(REPORT_HOUR, REPORT_MINUTE, tzinfo=IST), name="nightly")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
