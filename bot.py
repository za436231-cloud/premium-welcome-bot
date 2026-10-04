
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, MessageHandler, filters


WELCOME_DELETE_SECONDS = 30

RULES_LINK = "https://t.me/YOUR_RULES"
ADMIN_LINK = "https://t.me/YOUR_ADMIN"


async def new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    if not update.message.new_chat_members:
        return

    for member in update.message.new_chat_members:

        if member.username:
            name = "@" + member.username
        else:
            name = member.first_name or "Friend"

        text = (
            "✨ 𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𝐓𝐎 𝐎𝐔𝐑 𝐆𝐑𝐎𝐔𝐏 ✨\n\n"
            "👋 𝐇𝐞𝐲 " + name + "!\n\n"
            "💎 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐨𝐮𝐫 𝐜𝐨𝐦𝐦𝐮𝐧𝐢𝐭𝐲.\n"
            "🤝 𝐖𝐞'𝐫𝐞 𝐠𝐥𝐚𝐝 𝐭𝐨 𝐡𝐚𝐯𝐞 𝐲𝐨𝐮 𝐡𝐞𝐫𝐞.\n\n"
            "🛡️ 𝐏𝐥𝐞𝐚𝐬𝐞 𝐫𝐞𝐚𝐝 𝐭𝐡𝐞 𝐫𝐮𝐥𝐞𝐬 𝐛𝐞𝐟𝐨𝐫𝐞 𝐩𝐚𝐫𝐭𝐢𝐜𝐢𝐩𝐚𝐭𝐢𝐧𝐠.\n\n"
            "👇 𝐂𝐡𝐨𝐨𝐬𝐞 𝐚𝐧 𝐨𝐩𝐭𝐢𝐨𝐧:"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "📜 𝐑𝐔𝐋𝐄𝐒",
                    url=RULES_LINK
                ),
                InlineKeyboardButton(
                    "👤 𝐀𝐃𝐌𝐈𝐍",
                    url=ADMIN_LINK
                )
            ]
        ]

        sent = await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await asyncio.sleep(WELCOME_DELETE_SECONDS)

        try:
            await sent.delete()
        except Exception:
            pass


def main():

    token = os.environ["BOT_TOKEN"]

    app = Application.builder().token(token).build()

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            new_member
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
