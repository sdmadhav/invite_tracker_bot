import json
import os
import logging
from collections import defaultdict
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from datetime import datetime, timedelta

group_meta = defaultdict(lambda: {"joined": [], "added": []})
# ==================== CONFIG ==================== #

DATA_FILE = "inviter_stats.json"
MIN_INVITES_REQUIRED = 5

# ==================== LOGGING ==================== #

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== GLOBAL STATE ==================== #

group_stats = defaultdict(lambda: defaultdict(int))
group_names = {}  # {group_id: group_title}
initialized_groups = set()

# ==================== STORAGE UTILS ==================== #

def save_stats():
    data = {
        "stats": {str(k): dict(v) for k, v in group_stats.items()},
        "names": group_names,
        "meta": group_meta
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def load_stats():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            stats = data.get("stats", {})
            names = data.get("names", {})
            meta = data.get("meta", {})
            for group_id, users in stats.items():
                group_stats[int(group_id)] = defaultdict(int, {int(k): v for k, v in users.items()})
            for gid, gname in names.items():
                group_names[int(gid)] = gname
            for gid, meta_data in meta.items():
                group_meta[int(gid)] = meta_data
    except FileNotFoundError:
        logger.warning("No inviter stats file found; starting fresh.")


# ==================== COMMAND HANDLERS ==================== #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respond to /start in private chat."""
    if update.effective_chat.type == "private":
        keyboard = [[InlineKeyboardButton("Join Our Group", url="https://t.me/afc_cares")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🤖 Bot is active and ready!\n\n"
            "I’ll track who invites whom in your group.\n"
            "You’ll earn rewards for inviting friends 🚀",
            reply_markup=reply_markup
        )

# ==================== PRIVATE LEADERBOARD ==================== #

async def private_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available groups to choose leaderboard from."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("ℹ️ Please use this command in private chat.")
        return

    if not group_stats:
        await update.message.reply_text("📊 No group data found yet!")
        return

    keyboard = []
    for gid, name in group_names.items():
        keyboard.append([InlineKeyboardButton(name, callback_data=f"priv_lb_{gid}")])
    keyboard.append([InlineKeyboardButton("« Close", callback_data="close")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "📍 Select a group to view its leaderboard:",
        reply_markup=reply_markup
    )

async def private_leaderboard_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display leaderboard for selected group in private chat."""
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split("_")[2])

    if chat_id not in group_stats or not group_stats[chat_id]:
        await query.edit_message_text("📊 No invites yet in this group.")
        return

    sorted_inviters = sorted(group_stats[chat_id].items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard_text = f"🏆 **Top Inviters — {group_names.get(chat_id, 'Group')}** 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (user_id, count) in enumerate(sorted_inviters):
        try:
            user = await context.bot.get_chat(user_id)
            name = user.first_name
        except Exception:
            name = "Unknown User"
        medal = medals[i] if i < 3 else f"{i+1}."
        leaderboard_text += f"{medal} {name}: {count} invite(s)\n"

    keyboard = [[InlineKeyboardButton("« Back", callback_data="priv_lb_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(leaderboard_text, reply_markup=reply_markup, parse_mode="Markdown")

async def private_leaderboard_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to group selection menu."""
    query = update.callback_query
    await query.answer()
    keyboard = []
    for gid, name in group_names.items():
        keyboard.append([InlineKeyboardButton(name, callback_data=f"priv_lb_{gid}")])
    keyboard.append([InlineKeyboardButton("« Close", callback_data="close")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("📍 Select a group to view its leaderboard:", reply_markup=reply_markup)

# ==================== INVITE TRACKER ==================== #

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new members joining the group."""
    message = update.message
    chat = message.chat
    chat_id = chat.id
    new_members = message.new_chat_members

    # Store group name
    group_names[chat_id] = chat.title or "Unnamed Group"
    save_stats()

    for member in new_members:
        if member.is_bot:
                continue
        joined_time = datetime.utcnow().isoformat()
        group_meta[chat_id]["joined"].append(joined_time)
        inviter_id = message.from_user.id if message.from_user.id != member.id else None
        inviter_name = message.from_user.first_name if inviter_id else None

        if inviter_id:
            group_meta[chat_id]["added"].append(joined_time)
            group_stats[chat_id][inviter_id] += 1
            save_stats()
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(
                f"🎉 Thank you {inviter_name} for adding {member.first_name}!\n\n"
                f"📊 You’ve added {group_stats[chat_id][inviter_id]} member(s) so far.\n"
                f"Keep inviting more friends to reach {MIN_INVITES_REQUIRED} invites! 🚀",
                reply_markup=reply_markup
            )
        else:
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(
                f"👋 Welcome {member.first_name}!\n\n"
                f"To participate actively, please invite {MIN_INVITES_REQUIRED} friends "
                f"and compete on the leaderboard! 🌱",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

# ==================== MESSAGE MONITOR ==================== #

async def enforce_invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user has invited enough friends before allowing normal participation."""
    message = update.message
    chat_id = message.chat_id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    if message.from_user.is_bot:
        return

    if chat_id not in initialized_groups:
        await initialize_group_data(update, context)
        initialized_groups.add(chat_id)

    user_invites = group_stats[chat_id].get(user_id, 0)
    if user_invites < MIN_INVITES_REQUIRED:
        remaining = MIN_INVITES_REQUIRED - user_invites
        keyboard = [
            [InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")],
            [InlineKeyboardButton("« Back", callback_data="close")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            f"⚠️ Hey {user_name}, you’ve invited **{user_invites}** friend(s) so far.\n\n"
            f"Please invite **{remaining}** more to fully unlock group access 💬",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# ==================== GROUP INITIALIZATION ==================== #

async def initialize_group_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch all current members once to build initial dataset if not available."""
    chat = update.effective_chat
    chat_id = chat.id
    group_names[chat_id] = chat.title or "Unnamed Group"

    if chat_id in group_stats:
        return

    logger.info(f"Initializing data for group {chat.title} ({chat_id})...")
    try:
        members = await context.bot.get_chat_administrators(chat_id)
        for admin in members:
            group_stats[chat_id][admin.user.id] = 0
        save_stats()
        logger.info(f"Initialized with {len(members)} members for {chat.title}")
    except Exception as e:
        logger.error(f"Error initializing group {chat_id}: {e}")

# ==================== LEADERBOARD ==================== #

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = int(query.data.split("_")[1])
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
        except Exception:
            name = "Unknown User"
        medal = medals[i] if i < 3 else f"{i+1}."
        leaderboard_text += f"{medal} {name}: {count} invite(s)\n"

    keyboard = [[InlineKeyboardButton("« Back", callback_data="close")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(leaderboard_text, reply_markup=reply_markup, parse_mode="Markdown")

async def close_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()

# ================= Stats ==================== #

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of groups to choose stats from."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("ℹ️ Use /stats in private chat.")
        return

    if not group_names:
        await update.message.reply_text("📊 No group data found.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"stats_{gid}")]
        for gid, name in group_names.items()
    ]
    keyboard.append([InlineKeyboardButton("« Close", callback_data="close")])
    await update.message.reply_text("📍 Select a group to view stats:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_group_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed group stats."""
    query = update.callback_query
    await query.answer()
    gid = int(query.data.split("_")[1])

    meta = group_meta.get(gid, {"joined": [], "added": []})
    total_joined = len(meta["joined"])
    total_added = len(meta["added"])

    # New in last 7 days
    now = datetime.utcnow()
    joined_7d = sum(1 for j in meta["joined"] if datetime.fromisoformat(j) > now - timedelta(days=7))
    added_7d = sum(1 for a in meta["added"] if datetime.fromisoformat(a) > now - timedelta(days=7))

    # Group summary
    stats_text = (
        f"📊 **Group Stats — {group_names.get(gid, 'Unknown Group')}**\n\n"
        f"👥 Total Members Joined: {total_joined}\n"
        f"➕ Total Invited Members: {total_added}\n"
        f"📈 Joined in Last 7 Days: {joined_7d}\n"
        f"🏅 Invited in Last 7 Days: {added_7d}\n"
        f"🏆 Active Inviters: {len(group_stats[gid])}\n"
        f"💬 Messages Monitored: Coming Soon!\n"
    )

    keyboard = [[InlineKeyboardButton("« Back", callback_data="stats_back")]]
    await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def stats_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to group list."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"stats_{gid}")]
        for gid, name in group_names.items()
    ]
    keyboard.append([InlineKeyboardButton("« Close", callback_data="close")])
    await query.edit_message_text("📍 Select a group to view stats:", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== MAIN ENTRY ==================== #

def main():
    load_stats()

    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("Error: BOT_TOKEN environment variable not set!")
        return

    app = Application.builder().token(token).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", private_leaderboard))

    # Group Handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enforce_invites))

    # Callback Handlers
    app.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CallbackQueryHandler(private_leaderboard_view, pattern="^priv_lb_"))
    app.add_handler(CallbackQueryHandler(private_leaderboard_back, pattern="^priv_lb_back$"))
    app.add_handler(CallbackQueryHandler(close_message, pattern="^close$"))
    # stats
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(show_group_stats, pattern="^stats_"))
    app.add_handler(CallbackQueryHandler(stats_back, pattern="^stats_back$"))

    logger.info("🤖 Bot is running with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()


