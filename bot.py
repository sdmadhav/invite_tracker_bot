import logging
import asyncio
import os
import json
from datetime import datetime, timedelta
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
# ⚙️ Configuration
# -------------------------------
REQUIRED_INVITES = 3  # Number of people a user must invite to message
CHECK_INVITE_REQUIREMENT = True  # Set to False to disable this feature

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
                "last_updated": datetime.utcnow(),
                "invite_requirement_enabled": True,
                "required_invites": REQUIRED_INVITES
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


def get_group_settings(group_id: int) -> dict:
    """Get group settings including invite requirements"""
    try:
        doc = db.collection("groups").document(str(group_id)).get()
        if doc.exists:
            return doc.to_dict()
        return {"invite_requirement_enabled": True, "required_invites": REQUIRED_INVITES}
    except Exception as e:
        logger.error(f"Error getting group settings: {e}")
        return {"invite_requirement_enabled": True, "required_invites": REQUIRED_INVITES}


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


def log_member_join(group_id: int, user_id: int, invited_by: int = None) -> None:
    """Log each member join event with timestamp"""
    try:
        ref = db.collection("groups").document(str(group_id)).collection("member_joins").document()
        ref.set({
            "user_id": user_id,
            "invited_by": invited_by,
            "joined_at": datetime.utcnow(),
            "is_invited": invited_by is not None
        })
        logger.info(f"Member join logged: Group {group_id}, User {user_id}, Invited by {invited_by}")
    except Exception as e:
        logger.error(f"Error logging member join: {e}")


def get_inviter_stats_from_db(group_id: int) -> dict:
    """Retrieve inviter statistics for a group from Firestore"""
    try:
        docs = db.collection("groups").document(str(group_id)).collection("inviters").stream()
        return {int(doc.id): doc.to_dict() for doc in docs}
    except Exception as e:
        logger.error(f"Error fetching inviter stats for group {group_id}: {e}")
        return {}


def get_user_invite_count(group_id: int, user_id: int) -> int:
    """Get the number of people a user has invited to a group"""
    try:
        doc = db.collection("groups").document(str(group_id)).collection("inviters").document(str(user_id)).get()
        if doc.exists:
            return doc.to_dict().get("invite_count", 0)
        return 0
    except Exception as e:
        logger.error(f"Error getting user invite count: {e}")
        return 0


def can_user_message(group_id: int, user_id: int) -> tuple[bool, int, int]:
    """
    Check if user has invited enough people to message
    Returns: (can_message, current_invites, required_invites)
    """
    try:
        settings = get_group_settings(group_id)
        
        if not settings.get("invite_requirement_enabled", True):
            return True, 0, 0
        
        required = settings.get("required_invites", REQUIRED_INVITES)
        current = get_user_invite_count(group_id, user_id)
        
        return current >= required, current, required
    except Exception as e:
        logger.error(f"Error checking user message permission: {e}")
        return True, 0, 0  # Default to allowing messages on error


def get_group_statistics(group_id: int) -> dict:
    """Get comprehensive statistics for a group"""
    try:
        now = datetime.utcnow()
        seven_days_ago = now - timedelta(days=7)
        
        # Get all member joins
        joins_ref = db.collection("groups").document(str(group_id)).collection("member_joins")
        all_joins = list(joins_ref.stream())
        
        # Calculate statistics
        total_members = len(all_joins)
        total_invited = sum(1 for doc in all_joins if doc.to_dict().get("is_invited", False))
        
        # Last 7 days stats
        recent_joins = [doc for doc in all_joins if doc.to_dict().get("joined_at", datetime.min) >= seven_days_ago]
        joined_last_7 = len(recent_joins)
        invited_last_7 = sum(1 for doc in recent_joins if doc.to_dict().get("is_invited", False))
        
        # Active inviters (invited at least 1 person)
        inviter_stats = get_inviter_stats_from_db(group_id)
        active_inviters = len([uid for uid, data in inviter_stats.items() if data.get("invite_count", 0) > 0])
        
        return {
            "total_members": total_members,
            "total_invited": total_invited,
            "joined_last_7": joined_last_7,
            "invited_last_7": invited_last_7,
            "active_inviters": active_inviters
        }
    except Exception as e:
        logger.error(f"Error getting group statistics: {e}")
        return {
            "total_members": 0,
            "total_invited": 0,
            "joined_last_7": 0,
            "invited_last_7": 0,
            "active_inviters": 0
        }


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
        "👋 *Welcome to the Inviter Tracking Bot\\!*\n\n"
        "I help track who invites members to groups and maintain leaderboards\\.\n\n"
        "*Available Commands:*\n"
        "• /start \\- Show this message\n"
        "• /register\\_group \\- Register this group \\(Group admins only\\)\n"
        "• /leaderboard \\- View top inviters\n"
        "• /mystats \\- View your invite statistics\n"
        "• /groupstats \\- View group statistics\n"
        "• /setrequirement \\<number\\> \\- Set invite requirement \\(Admins only\\)\n\n"
        "⚠️ *Note:* To message in groups, you must invite the required number of friends first\\!\n\n"
        "**Just add me to your group and make me an admin**\\! 🚀"
    )
    await update.message.reply_text(welcome_text, parse_mode="MarkdownV2")


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
            f"⚠️ *Invite Requirement:* Members must invite {REQUIRED_INVITES} friends to message in this group.\n\n"
            f"Use /setrequirement to change this number.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error registering group: {e}")
        await update.message.reply_text("❌ Error registering group. Please try again.")


async def set_requirement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the invite requirement for a group (admin only)"""
    chat = update.effective_chat
    
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ This command only works in groups.")
        return
    
    # Check if user is admin
    try:
        member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
        if member.status not in ["creator", "administrator"]:
            await update.message.reply_text("❌ Only group administrators can change settings.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return
    
    # Parse the number
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ Usage: /setrequirement <number>\n\n"
            "Example: /setrequirement 5\n"
            "Set to 0 to disable invite requirement."
        )
        return
    
    try:
        required_invites = int(context.args[0])
        if required_invites < 0:
            await update.message.reply_text("❌ Number must be 0 or positive.")
            return
        
        # Update in database
        db.collection("groups").document(str(chat.id)).update({
            "required_invites": required_invites,
            "invite_requirement_enabled": required_invites > 0
        })
        
        if required_invites == 0:
            await update.message.reply_text(
                "✅ *Invite requirement disabled!*\n\n"
                "All members can now message freely.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"✅ *Invite requirement updated!*\n\n"
                f"Members must now invite *{required_invites}* friend(s) to message in this group.",
                parse_mode="Markdown"
            )
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number.")
    except Exception as e:
        logger.error(f"Error setting requirement: {e}")
        await update.message.reply_text("❌ Error updating settings.")


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
                log_member_join(chat_id, member.id, invited_by=inviter_id)
                
                settings = get_group_settings(chat_id)
                required = settings.get("required_invites", REQUIRED_INVITES)
                
                keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Check if user can now message
                if new_count >= required:
                    status_msg = f"🎉 You can now message in this group!"
                else:
                    remaining = required - new_count
                    status_msg = f"📝 Invite {remaining} more friend(s) to message in the group."
                
                await message.reply_text(
                    f"🎉 *Thank you {inviter_name} for adding {member.first_name}!*\n\n"
                    f"📊 You've invited *{new_count}* member(s) to the group.\n"
                    f"{status_msg}\n"
                    f"Keep inviting friends! 🚀",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error handling new member: {e}")
        else:
            # User joined via link
            log_member_join(chat_id, member.id, invited_by=None)
            
            settings = get_group_settings(chat_id)
            required = settings.get("required_invites", REQUIRED_INVITES)
            
            if required > 0:
                await message.reply_text(
                    f"👋 *Welcome {member.first_name}!*\n\n"
                    f"⚠️ To message in this group, you must invite *{required}* friend(s) first.\n\n"
                    f"📢 Add your friends to unlock messaging privileges!",
                    parse_mode="Markdown"
                )
            else:
                await message.reply_text(
                    f"👋 *Welcome {member.first_name}!*\n\n"
                    f"📢 Help us grow this community by inviting your friends!",
                    parse_mode="Markdown"
                )


async def check_message_permission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check if user can message and delete message if they can't"""
    message = update.message
    chat_id = message.chat_id
    user_id = message.from_user.id
    
    # Skip for admins and bot
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ["creator", "administrator"]:
            return  # Admins can always message
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return
    
    # Check if user has invited enough people
    can_message, current, required = can_user_message(chat_id, user_id)
    
    if not can_message:
        try:
            # Delete the message
            await message.delete()
            
            # Calculate remaining invites
            remaining = required - current
            
            # Send notification
            notification = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ {message.from_user.first_name}, you need to invite *{remaining}* more friend(s) to message in this group.\n\n"
                    f"📊 Current invites: {current}/{required}\n"
                    f"💡 Add friends to the group to unlock messaging!"
                ),
                parse_mode="Markdown"
            )
            
            # Delete notification after 10 seconds
            await asyncio.sleep(10)
            try:
                await notification.delete()
            except:
                pass
                
        except Exception as e:
            logger.error(f"Error handling restricted message: {e}")


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
                    name = str(user_id)
            
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
            name = data.get("user_name", str(user_id))
            
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
            
            # Check if they can message
            settings = get_group_settings(group_id)
            required = settings.get("required_invites", REQUIRED_INVITES)
            status = "✅" if count >= required else f"❌ ({required - count} more needed)"
            
            stats_text += f"• {group_data['group_name']}: *{count}* invite(s) {status}\n"
            found_stats = True
    
    if not found_stats:
        await update.message.reply_text("📊 You haven't invited anyone yet. Start inviting friends!")
        return
    
    stats_text += f"\n🎯 *Total Invites: {total_invites}*"
    await update.message.reply_text(stats_text, parse_mode="Markdown")


async def group_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show statistics for current group or all groups"""
    chat = update.effective_chat
    
    # If in a group, show that group's stats
    if chat.type in ["group", "supergroup"]:
        groups = get_all_groups_from_db()
        if chat.id not in groups:
            await update.message.reply_text("❌ This group is not registered. Use /register_group first.")
            return
        
        group_data = groups[chat.id]
        stats = get_group_statistics(chat.id)
        settings = get_group_settings(chat.id)
        
        stats_text = f"📊 *Group Stats — {group_data['group_name']}*\n\n"
        stats_text += f"👥 Total Members Joined: *{stats['total_members']}*\n"
        stats_text += f"➕ Total Invited Members: *{stats['total_invited']}*\n"
        stats_text += f"📈 Joined in Last 7 Days: *{stats['joined_last_7']}*\n"
        stats_text += f"🏅 Invited in Last 7 Days: *{stats['invited_last_7']}*\n"
        stats_text += f"🏆 Active Inviters: *{stats['active_inviters']}*\n\n"
        
        required = settings.get("required_invites", REQUIRED_INVITES)
        if required > 0:
            stats_text += f"⚠️ Invite Requirement: *{required}* friend(s)"
        else:
            stats_text += f"✅ Invite Requirement: *Disabled*"
        
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return
    
    # If in DM, show all groups statistics
    if chat.type != "private":
        await update.message.reply_text("❌ This command works in groups or private messages.")
        return
    
    groups = get_all_groups_from_db()
    
    if not groups:
        await update.message.reply_text("📊 No registered groups found.")
        return
    
    stats_text = "📈 *Bot Group Statistics*\n\n"
    
    for group_id, group_data in groups.items():
        group_name = group_data["group_name"]
        stats = get_group_statistics(group_id)
        
        stats_text += f"*{group_name}*\n"
        stats_text += f"  👥 Members: {stats['total_members']}\n"
        stats_text += f"  ➕ Invited: {stats['total_invited']}\n"
        stats_text += f"  🏆 Inviters: {stats['active_inviters']}\n\n"
    
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
    app.add_handler(CommandHandler("setrequirement", set_requirement))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    
    # Check all regular messages for invite requirement
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        check_message_permission
    ))
    
    # Callback query handlers
    app.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CallbackQueryHandler(close_message, pattern="^close$"))
    
    # Start bot
    logger.info("🤖 Bot started with Firebase backend and invite requirements...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
