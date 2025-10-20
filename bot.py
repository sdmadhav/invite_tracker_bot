import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from firebase_admin import firestore, initialize_app, credentials
from datetime import datetime
import os
import json
# -------------------------------
# 🔥 Firebase Initialization
# -------------------------------

firebase_json = os.getenv("FIREBASE_CREDENTIALS")

if firebase_json:
    cred_dict = json.loads(firebase_json)
    cred = credentials.Certificate(cred_dict)
else:
    print("Variable not found")

try:
    initialize_app(cred)
except ValueError:
    pass  # already initialized

db = firestore.client()


# -------------------------------
# ⚡ Logging
# -------------------------------
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(message)s", level=logging.INFO
)

# -------------------------------
# ⚡ Global Cache
# -------------------------------
cache = {
    "groups": {},  # group_id -> {name, stats, last_updated}
    "leaderboards": {},  # group_id -> leaderboard data
}


# -------------------------------
# 🚀 Firestore Helpers
# -------------------------------
def save_group_to_db(group_id, group_name):
    """Store or update group in Firestore"""
    ref = db.collection("groups").document(str(group_id))
    ref.set(
        {"group_name": group_name, "added_on": datetime.utcnow()},
        merge=True,
    )


def get_all_groups_from_db():
    """Return all groups from Firestore"""
    docs = db.collection("groups").stream()
    return {doc.id: doc.to_dict() for doc in docs}


def save_leaderboard_to_db(group_id, leaderboard_data):
    """Save leaderboard stats"""
    ref = db.collection("leaderboards").document(str(group_id))
    ref.set({"data": leaderboard_data, "last_updated": datetime.utcnow()})


def get_leaderboard_from_db(group_id):
    doc = db.collection("leaderboards").document(str(group_id)).get()
    return doc.to_dict() if doc.exists else None


# -------------------------------
# 🤖 Bot Commands
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! I'm your tracking bot!")


async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register group when bot becomes admin"""
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        group_id = chat.id
        group_name = chat.title

        # Save in Firestore
        save_group_to_db(group_id, group_name)

        # Update cache
        cache["groups"][group_id] = {"group_name": group_name}

        await update.message.reply_text(f"✅ Group '{group_name}' registered successfully!")
        logging.info(f"Group registered: {group_name} ({group_id})")
    else:
        await update.message.reply_text("❌ This command only works in groups.")


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show groups where bot is admin"""
    user_id = update.effective_user.id

    # Load from cache if available, else from Firestore
    if not cache["groups"]:
        cache["groups"] = get_all_groups_from_db()

    if not cache["groups"]:
        await update.message.reply_text("📊 No group data found.")
        return

    # Create buttons for each group
    buttons = [
        [InlineKeyboardButton(g["group_name"], callback_data=f"leaderboard_{gid}")]
        for gid, g in cache["groups"].items()
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Select a group:", reply_markup=reply_markup)


async def show_group_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle group selection and show leaderboard"""
    query = update.callback_query
    await query.answer()

    try:
        group_id = query.data.split("_")[1]

        # Check cache first
        data = cache["leaderboards"].get(group_id)
        if not data:
            # Load from Firestore
            doc = get_leaderboard_from_db(group_id)
            if doc:
                data = doc["data"]
                cache["leaderboards"][group_id] = data
            else:
                await query.edit_message_text("📊 No leaderboard data found for this group.")
                return

        # Display leaderboard
        text = f"🏆 *Leaderboard for {cache['groups'][int(group_id)]['group_name']}*:\n\n"
        for i, (user, score) in enumerate(data.items(), start=1):
            text += f"{i}. {user}: {score}\n"

        await query.edit_message_text(text, parse_mode="Markdown")

    except Exception as e:
        logging.exception(e)
        await query.edit_message_text("⚠️ Error fetching leaderboard.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show general stats for each group"""
    if not cache["groups"]:
        cache["groups"] = get_all_groups_from_db()

    if not cache["groups"]:
        await update.message.reply_text("📊 No group data found.")
        return

    text = "📈 *Bot Group Stats*\n\n"
    for gid, g in cache["groups"].items():
        group_name = g["group_name"]
        leaderboard_data = get_leaderboard_from_db(gid)
        members = len(leaderboard_data["data"]) if leaderboard_data else 0
        text += f"• {group_name}: {members} members tracked\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# -------------------------------
# 🧠 Main Function
# -------------------------------
def main():
    token = os.environ.get('BOT_TOKEN')
    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register_group", register_group))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(show_group_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CommandHandler("stats", stats))

    logging.info("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()



