import logging
import asyncio
import os
import json
from datetime import datetime
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from firebase_admin import firestore, initialize_app, credentials

# -------------------------------
# 🔥 Firebase Initialization
# -------------------------------
firebase_json = os.getenv("FIREBASE_CREDENTIALS")

if firebase_json:
    try:
        cred_dict = json.loads(firebase_json)
        cred = credentials.Certificate(cred_dict)
        initialize_app(cred)
        logging.info("Firebase initialized successfully")
    except ValueError as e:
        if "already initialized" not in str(e).lower():
            logging.error(f"Firebase initialization error: {e}")
        pass  # already initialized
    except Exception as e:
        logging.error(f"Firebase setup error: {e}")
        raise
else:
    logging.error("FIREBASE_CREDENTIALS environment variable not found")
    raise ValueError("Firebase credentials not configured")

db = firestore.client()

# -------------------------------
# ⚡ Logging Configuration
# -------------------------------
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------------
# 📊 Database Operations
# -------------------------------
def save_group_to_db(group_id: int, group_name: str) -> None:
    """Store or update group information in Firestore"""
    try:
        ref = db.collection("groups").document(str(group_id))
        ref.set(
            {
                "group_name": group_name,
                "group_id": group_id,
                "added_on": datetime.utcnow(),
                "last_updated": datetime.utcnow()
            },
            merge=True,
        )
        logger.info(f"Group saved: {group_name} ({group_id})")
    except Exception as e:
        logger.error(f"Error saving group {group_id}: {e}")
        raise


def get_all_groups_from_db() -> dict:
    """Retrieve all groups from Firestore"""
    try:
        docs = db.collection("groups").stream()
        return {int(doc.id): doc.to_dict() for doc in docs}
    except Exception as e:
        logger.error(f"Error fetching groups: {e}")
        return {}


def save_inviter_stats_to_db(group_id: int, user_id: int, count: int, user_name: str = None) -> None:
    """Save inviter statistics to Firestore"""
    try:
        ref = db.collection("groups").document(str(group_id)).collection("inviters").document(str(user_id))
        data = {
            "user_id": user_id,
            "invite_count": count,
            "last_updated": datetime.utcnow()
        }
        if user_name:
            data["user_name"] = user_name
        ref.set(data, merge=True)
        logger.info(f"Inviter stats saved: Group {group_id}, User {user_id}, Count {count}")
    except Exception as e:
        logger.error(f"Error saving inviter stats: {e}")


def get_inviter_stats_from_db(group_id: int) -> dict:
    """Retrieve inviter statistics for a group from Firestore"""
    try:
        docs = db.collection("groups").document(str(group_id)).collection("inviters").stream()
        return {int(doc.id): doc.to_dict() for doc in docs}
    except Exception as e:
        logger.error(f"Error fetching inviter stats for group {group_id}: {e}")
        return {}


def increment_inviter_count(group_id: int, user_id: int, user_name: str = None) -> int:
    """Atomically increment inviter count in Firestore and return new count"""
    try:
        ref = db.collection("groups").document(str(group_id)).collection("inviters").document(str(user_id))
        
        # Use transaction for atomic increment
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            new_count = snapshot.get("invite_count") + 1 if snapshot.exists else 1
            
            data = {
                "user_id": user_id,
                "invite_count": new_count,
                "last_updated": datetime.utcnow()
            }
            if user_name:
                data["user_name"] = user_name
            
            transaction.set(doc_ref, data, merge=True)
            return new_count
        
        transaction = db.transaction()
        new_count = update_in_transaction(transaction, ref)
        
        # Also update group's last activity
        db.collection("groups").document(str(group_id)).update({"last_updated": datetime.utcnow()})
        
        return new_count
    except Exception as e:
        logger.error(f"Error incrementing inviter count: {e}")
        return 0


# -------------------------------
# 🤖 Bot Command Handlers
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    welcome_text = (
        "👋 *Welcome to the Inviter Tracking Bot!*\n\n"
        "I help track who invites members to groups and maintain leaderboards.\n\n"
        "*Available Commands:*\n"
        "• /start - Show this message\n"
        "• /register_group - Register this group (Group admins only)\n"
        "• /leaderboard - View top inviters\n"
        "• /mystats - View your invite statistics\n"
        "• /groupstats - View all group statistics (DM only)\n\n"
        "Just add me to your group and make me an admin! 🚀"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register a group when bot becomes admin"""
    chat = update.effective_chat
    
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ This command only works in groups.")
        return
    
    # Check if user is admin
    try:
        member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
        if member.status not in ["creator", "administrator"]:
            await update.message.reply_text("❌ Only group administrators can register the group.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Error checking permissions.")
        return
    
    group_id = chat.id
    group_name = chat.title
    
    try:
        save_group_to_db(group_id, group_name)
        await update.message.reply_text(
            f"✅ *Group Registered Successfully!*\n\n"
            f"📊 Group: {group_name}\n"
            f"🆔 ID: `{group_id}`\n\n"
            f"I'll now track all new member invites!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error registering group: {e}")
        await update.message.reply_text("❌ Error registering group. Please try again.")


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle new members joining the group"""
    message = update.message
    chat_id = message.chat_id
    new_members = message.new_chat_members
    
    # Ensure group is registered
    groups = get_all_groups_from_db()
    if chat_id not in groups:
        save_group_to_db(chat_id, message.chat.title)
    
    for member in new_members:
        # Skip if bot itself was added
        if member.is_bot:
            continue
        
        # Check if user was added by someone or joined via link
        if message.from_user.id != member.id:
            # User was added by another user
            inviter_id = message.from_user.id
            inviter_name = message.from_user.first_name
            
            try:
                new_count = increment_inviter_count(chat_id, inviter_id, inviter_name)
                
                keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await message.reply_text(
                    f"🎉 *Thank you {inviter_name} for adding {member.first_name}!*\n\n"
                    f"📊 You've invited *{new_count}* member(s) to the group.\n"
                    f"Keep inviting friends! 🚀",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error handling new member: {e}")
        else:
            # User joined via link
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                f"👋 *Welcome {member.first_name}!*\n\n"
                f"📢 Help us grow this community by inviting your friends!\n"
                f"✨ Add members and compete on the leaderboard!",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )


async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display leaderboard for a specific group"""
    query = update.callback_query
    await query.answer()
    
    try:
        chat_id = int(query.data.split('_')[1])
        
        inviter_stats = get_inviter_stats_from_db(chat_id)
        
        if not inviter_stats:
            await query.edit_message_text("📊 No invites yet. Be the first to invite friends!")
            return
        
        # Sort by invite count
        sorted_inviters = sorted(
            inviter_stats.items(),
            key=lambda x: x[1].get("invite_count", 0),
            reverse=True
        )[:10]
        
        # Get group name
        groups = get_all_groups_from_db()
        group_name = groups.get(chat_id, {}).get("group_name", "Unknown Group")
        
        leaderboard_text = f"🏆 *Top Inviters - {group_name}* 🏆\n\n"
        medals = ["🥇", "🥈", "🥉"]
        
        for i, (user_id, data) in enumerate(sorted_inviters):
            count = data.get("invite_count", 0)
            
            # Try to get user name from stored data or fetch from Telegram
            name = data.get("user_name")
            if not name:
                try:
                    user = await context.bot.get_chat(user_id)
                    name = user.first_name
                except:
                    name = "Unknown User"
            
            medal = medals[i] if i < 3 else f"{i+1}."
            leaderboard_text += f"{medal} {name}: *{count}* invite(s)\n"
        
        keyboard = [[InlineKeyboardButton("« Close", callback_data="close")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            leaderboard_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error showing leaderboard: {e}")
        await query.edit_message_text("⚠️ Error loading leaderboard. Please try again.")


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show leaderboard command handler"""
    chat = update.effective_chat
    
    if chat.type in ["group", "supergroup"]:
        # In group, show that group's leaderboard
        chat_id = chat.id
        inviter_stats = get_inviter_stats_from_db(chat_id)
        
        if not inviter_stats:
            await update.message.reply_text("📊 No invites yet. Be the first to invite friends!")
            return
        
        sorted_inviters = sorted(
            inviter_stats.items(),
            key=lambda x: x[1].get("invite_count", 0),
            reverse=True
        )[:10]
        
        leaderboard_text = f"🏆 *Top Inviters - {chat.title}* 🏆\n\n"
        medals = ["🥇", "🥈", "🥉"]
        
        for i, (user_id, data) in enumerate(sorted_inviters):
            count = data.get("invite_count", 0)
            name = data.get("user_name", "Unknown User")
            
            medal = medals[i] if i < 3 else f"{i+1}."
            leaderboard_text += f"{medal} {name}: *{count}* invite(s)\n"
        
        await update.message.reply_text(leaderboard_text, parse_mode="Markdown")
    else:
        # In private chat, show list of groups
        groups = get_all_groups_from_db()
        
        if not groups:
            await update.message.reply_text("📊 No registered groups found.")
            return
        
        buttons = [
            [InlineKeyboardButton(g["group_name"], callback_data=f"leaderboard_{gid}")]
            for gid, g in groups.items()
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text("Select a group to view leaderboard:", reply_markup=reply_markup)


async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's invite statistics across all groups"""
    user_id = update.effective_user.id
    groups = get_all_groups_from_db()
    
    if not groups:
        await update.message.reply_text("📊 No groups registered yet.")
        return
    
    stats_text = f"📊 *Your Invite Statistics*\n\n"
    total_invites = 0
    found_stats = False
    
    for group_id, group_data in groups.items():
        inviter_stats = get_inviter_stats_from_db(group_id)
        if user_id in inviter_stats:
            count = inviter_stats[user_id].get("invite_count", 0)
            total_invites += count
            stats_text += f"• {group_data['group_name']}: *{count}* invite(s)\n"
            found_stats = True
    
    if not found_stats:
        await update.message.reply_text("📊 You haven't invited anyone yet. Start inviting friends!")
        return
    
    stats_text += f"\n🎯 *Total Invites: {total_invites}*"
    await update.message.reply_text(stats_text, parse_mode="Markdown")


async def group_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show statistics for all groups (DM only)"""
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ This command only works in private messages.")
        return
    
    groups = get_all_groups_from_db()
    
    if not groups:
        await update.message.reply_text("📊 No registered groups found.")
        return
    
    stats_text = "📈 *Bot Group Statistics*\n\n"
    
    for group_id, group_data in groups.items():
        group_name = group_data["group_name"]
        inviter_stats = get_inviter_stats_from_db(group_id)
        
        total_inviters = len(inviter_stats)
        total_invites = sum(data.get("invite_count", 0) for data in inviter_stats.values())
        
        stats_text += f"*{group_name}*\n"
        stats_text += f"  👥 Inviters: {total_inviters}\n"
        stats_text += f"  📊 Total Invites: {total_invites}\n\n"
    
    await update.message.reply_text(stats_text, parse_mode="Markdown")


async def close_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close/delete message"""
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")


# -------------------------------
# 🧠 Main Function
# -------------------------------
def main():
    """Initialize and run the bot"""
    token = os.environ.get('BOT_TOKEN')
    
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        raise ValueError("Bot token not configured")
    
    # Build application
    app = Application.builder().token(token).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register_group", register_group))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("mystats", my_stats))
    app.add_handler(CommandHandler("groupstats", group_stats))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    
    # Callback query handlers
    app.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CallbackQueryHandler(close_message, pattern="^close$"))
    
    # Start bot
    logger.info("🤖 Bot started with Firebase backend...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
