import os, re, time, random, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

# ================= SETTINGS =================
BOT_TOKEN = os.environ["8842649945:AAHt86wgpJ0UCGRIW7G7zvpUKw-9ON3Rndk"]
BOT_USERNAME = "rizzlerxescrow"   # apne bot ka username (@ ke bina)
ADMIN_IDS = [7054168366]          # admins ki baat-cheet mein bot nahi ghusega
STAGE_TIMEOUT = 180               # 3 min ke andar jawab aaye tabhi agla step chalega
PITCH_COOLDOWN = 600              # ek member ko 10 min mein sirf 1 baar deal wali baat
# ============================================

GREET = re.compile(
    r"\b(hi+|hello+|hey+|hlo+|helo|namaste|salam|assalam\w*|good morning|good evening|gm|ram ram|jai shree \w+)\b",
    re.I,
)
DEAL = re.compile(
    r"\b(deal|escrow|buy|buying|sell|selling|trade|exchange|payment)\b", re.I
)

GREET_REPLIES = [
    "Hello {n}! 😊 Kaise ho? Kya haal chaal?",
    "Arre {n}! 👋 Kaise ho bhai, sab badhiya?",
    "Hey {n}! 😄 Haal-chaal batao, kya chal raha hai?",
]
HAL_REPLIES = [
    "Badhiya! 😄 Aur batao, koi deal-vaal karni hai kya?",
    "Mast! 🔥 Koi buy/sell ka plan hai aaj?",
    "Ye sunke achha laga 😊 Kuch deal karni ho to bata dena.",
]
PITCH = (
    "🔒 Ek baat yaad rakhna {n}, deal <b>yahin escrow ke through</b> karna.\n"
    "Isse aapka paisa aur maal dono safe rehte hain ✅"
)


def mention(u):
    return f'<a href="tg://user?id={u.id}">{u.first_name}</a>'


def pitch_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📝 Deal Form", url=f"https://t.me/{BOT_USERNAME}?start=form")
    ]])


async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if not msg or not msg.text or not user or user.is_bot or user.id in ADMIN_IDS:
        return

    text = msg.text
    ud = context.user_data
    now = time.time()
    stage = ud.get("stage")
    fresh = bool(stage) and now - ud.get("stage_t", 0) < STAGE_TIMEOUT

    async def send_pitch():
        if now - ud.get("pitch_t", 0) < PITCH_COOLDOWN:
            return
        ud["pitch_t"] = now
        ud["stage"] = None
        await msg.reply_text(
            PITCH.format(n=mention(user)),
            parse_mode=ParseMode.HTML,
            reply_markup=pitch_kb(),
        )

    # Step 3: seedha deal ki baat likhi
    if DEAL.search(text):
        await send_pitch()
        return

    # Step 2: haal puchha tha, ab jawab aaya
    if fresh and stage == "asked_hal":
        ud["stage"], ud["stage_t"] = "asked_deal", now
        await msg.reply_text(random.choice(HAL_REPLIES))
        return

    # Step 2b: deal puchhi thi, koi bhi jawab aaye to pitch kar do
    if fresh and stage == "asked_deal":
        await send_pitch()
        return

    # Step 1: hi/hello aaya
    if GREET.search(text):
        ud["stage"], ud["stage_t"] = "asked_hal", now
        await msg.reply_text(
            random.choice(GREET_REPLIES).format(n=mention(user)),
            parse_mode=ParseMode.HTML,
        )


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, talk))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
