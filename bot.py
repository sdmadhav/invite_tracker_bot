from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import json
from collections import defaultdict
import os

# Store inviter stats per group: {group_id: {user_id: count}}
group_stats = defaultdict(lambda: defaultdict(int))

def save_stats():
    # Convert nested defaultdict to regular dict for JSON
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

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    new_members = message.new_chat_members
    
    for member in new_members:
        # Skip if bot itself was added
        if member.is_bot:
            continue
            
        # Check if user was added by someone or joined via link
        if message.from_user.id != member.id:
            # User was added by another user
            inviter_id = message.from_user.id
            inviter_name = message.from_user.first_name
            group_stats[chat_id][inviter_id] += 1
            save_stats()
            
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                f"🎉 Thank you {inviter_name} for adding {member.first_name}!\n\n"
                f"📊 You've added {group_stats[chat_id][inviter_id]} member(s) to the group.\n"
                f"Keep inviting friends! 🚀",
                reply_markup=reply_markup
            )
        else:
            # User joined via link
            keyboard = [[InlineKeyboardButton("🏆 View Leaderboard", callback_data=f"leaderboard_{chat_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                f"👋 Welcome {member.first_name}!\n\n"
                f"📢 **Important Group Rule:**\n"
                f"To avoid bans, this group cannot be made public. We need your help to grow! 🌱\n\n"
                f"✨ Add your friends and compete on the leaderboard!",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Extract chat_id from callback data
    chat_id = int(query.data.split('_')[1])
    
    if chat_id not in group_stats or not group_stats[chat_id]:
        await query.edit_message_text("📊 No invites yet. Be the first to invite friends!")
        return
    
    # Sort by invites for this specific group
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

def main():
    load_stats()
    
    # Get token from environment variable
    token = os.environ.get('BOT_TOKEN')
    
    if not token:
        print("Error: BOT_TOKEN environment variable not set!")
        return
    
    app = Application.builder().token(token).build()
    
    # Handler for new members
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    
    # Handler for leaderboard button (pattern matches leaderboard_*)
    app.add_handler(CallbackQueryHandler(show_leaderboard, pattern="^leaderboard_"))
    app.add_handler(CallbackQueryHandler(close_message, pattern="^close$"))
    
    # Use polling - works perfectly on Railway
    print("Bot is running with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()