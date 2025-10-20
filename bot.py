from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import json
from collections import defaultdict
import os

# Store inviter stats per group: {group_id: {user_id: count}}
group_stats = defaultdict(lambda: defaultdict(int))
# Store whether we’ve initialized a group (loaded existing members)
initialized_groups = set()

def save_stats():
    data = {str(k): dict(v) for k, v in group_stats.items()}
    with open('inviter_stats.json', 'w') as f:
        json.dump(data, f)

def load_stats():
    try:
        with open('inviter_stats.json', 'r') as f:
            data = json.load(f)
            for group_id, users in data.items():
                group_stats[int(group_id)] = defaultdict(int, {int(k): v for k, v in users.items()})
    except FileNotFoundError:
        pass

# ----------------------------
# 1. Handle when bot joins group
# ----------------------------
async def handle_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat
    chat_id = chat.id

    if chat_id not in initialized_groups:
        initialized_groups.add(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🤖 Hello everyone! I'm here to track and reward your group growth!\n\n"
                f"🌱 Here's how it works:\n"
                f"1️⃣ Add your friends to this group.\n"
                f"2️⃣ Each invite earns you leaderboard points.\n"
                f"3️⃣ Compete to be among the top inviters! 🏆\n\n"
                f"Let's grow this community together 🚀"
            )
        )

        # Fetch existing members just once
        try:
            members = await context.bot.get_chat_administrators(chat_id)
            for admin in members:
                group_stats[chat_id][admin.user.id] = 0
            save_stats()
        except Exception as e:
            print(f"Could not fetch members: {e}")

# ----------------------------
# 2. Handle new members joining
# ----------------------------
async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    new_members = message.new_chat_members

    for member in new_members:
        if member.is_bot:
            continue

        if message.from_user.id != member.id:
            inviter_id = message.from_user.id
            inviter_name = message.from_user.first_name
            group_stats[chat_id][inviter_id] += 1
            save_stats()

            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await message.reply_text(
                f"🎉 Thank you {inviter_name} for adding {member.first_name}!\n\n"
                f"📊 You've added {group_stats[chat_id][inviter_id]} member(s).\n"
                f"Keep inviting friends! 🚀",
                reply_markup=reply_markup
            )
        else:
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await message.reply_text(
                f"👋 Welcome {member.first_name}!\n\n"
                f"📢 **Group Rule:** To stay in the group, invite 5 friends!\n\n"
                f"✨ Help us grow and climb the leaderboard 🏆",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

# ----------------------------
# 3. Reply to messages from users
# ----------------------------
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    # Ignore bot messages
    if message.from_user.is_bot:
        return

    # Initialize user in stats if missing
    if user_id not in group_stats[chat_id]:
        group_stats[chat_id][user_id] = 0
        save_stats()

    invites = group_stats[chat_id][user_id]
    remaining = 5 - invites

    if remaining > 0:
        await message.reply_text(
            f"👋 {user_name}, you’ve invited {invites} friends so far.\n"
            f"🔑 Invite {remaining} more friends to unlock full group access!",
        )

# ----------------------------
# 4. Leaderboard + close
# ----------------------------
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split('_')[1])

    if chat_id not in group_stats or not group_stats[chat_id]:
        await query.edit_message_text("📊 No invites yet. Be the first to invite friends!")
        return

    sorted_inviters = sorted(group_stats[chat_id].items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard_text = "🏆 **Top Inviters Leaderboard** 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (user_id, count) in enumerate(sorted_inviters):
        try:
            user = await context.bot.get_chat(user_id)
            name = user.first_name
        except:
            name = "Unknown User"
        medal = medals[i] if i < 3 else f"{i+1}."
        leaderboard_text += f"{medal} {name}: {count} invite(s)\n"

    keyboard = [[InlineKeyboardButton("« Back", callback_data="close")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(leaderboard_text, reply_markup=reply_markup, parse_mode='Markdown')

async def close_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()

# ----------------------------
# 5. Main
# ----------------------------
def main():
    load_stats()
    token = os.environ.get('BOT_TOKEN')
    if not token:
        print("Error: BOT_TOKEN environment variable not set!")
        return

    app = Application.builder().token(token).build()

    # Bot added
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.ChatType.GROUPS, handle_bot_added))

    # New members
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))

    # Normal user messages
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_message))

    # Leaderboard
    app.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CallbackQueryHandler(close_message, pattern="^close$"))

    print("Bot is running with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
