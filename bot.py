import sqlite3
import os
import re
from dotenv import load_dotenv, find_dotenv 
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatMemberStatus

# --- 1. SETUP AND CONFIGURATION ---

# Load environment variables (like BOT_TOKEN) from the .env file
load_dotenv(find_dotenv(usecwd=True, raise_error_if_not_found=False)) 
BOT_TOKEN = os.getenv("BOT_TOKEN")

# XP & Penalty Values
XP_PER_MESSAGE = 2
INFRACTION_VALUE = 50  
TRUST_LOSS_PER_INFRACTION = 5
INFRACTION_PENALTY_XP = -100

# Banned Words List (ENGLISH & HINDI SLANGS/CURSE WORDS)
# NOTE: This list is illustrative. You must maintain and expand this list yourself 
# to ensure compliance with local laws and platform TOS.
BANNED_WORDS = [
    # English Slangs/Cursing
    "asshole", "bitch", "cunt", "damn", "fucker", "fuck", "shit", "bastard", 
    "piss off", "wanker", "moron", "idiot", "retard", "gay", "nigger", "crap",
    # Hindi/Hinglish Slangs/Cursing (Phonetic spelling used)
    "madarchod", "behenchod", "bhadwa", "chutiya", "randi", "saala", "kutta",
    "gandu", "bc", "mc", "teri maa", "gaand", "harami", "lund", "suar",
    # General Hate/Aggression
    "kill", "hate", "die", "stupid", "worthless", "loser", "ugly", 
]

# Rank Thresholds 
HERO_RANKS = {
    0: "F-Class Rookie", 51: "E-Class Apprentice", 151: "D-Class Warrior", 
    301: "C-Class Guardian", 601: "B-Class Champion", 1001: "A-Class Veteran",
    2001: "S-Class Supreme", 3501: "SS-Class Ascendant", 6001: "SSS-Class Divine", 
    10001: "EX-Class Eternal",
}

VILLAIN_RANKS = {
    0: "F-Villain Worm", 51: "E-Villain Menace", 151: "D-Villain Corrupt",
    301: "C-Villain Blight", 601: "B-Villain Scourge", 1001: "A-Villain Plague", 
    2001: "S-Villain Disgraced", 3501: "SS-Villain Exiled", 6001: "SSS-Villain Abomination",
    10001: "EX-Villain Forsaken",
}

# --- 2. DATABASE FUNCTIONS (SQLite) ---
DB_NAME = "rankify_db.sqlite"

def init_db():
    """Initializes the database and user table."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            xp INTEGER DEFAULT 0,
            infractions INTEGER DEFAULT 0,
            legacy_xp INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def update_user_xp(user_id, username, xp_gain=0, infraction_gain=0):
    """Adds XP or infractions to a user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT xp, infractions, legacy_xp FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        # New user insertion
        cursor.execute("""
            INSERT INTO users (user_id, username, xp, infractions, legacy_xp) 
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, xp_gain, infraction_gain, max(0, xp_gain)))
    else:
        # Existing user update
        new_xp = row[0] + xp_gain
        new_infractions = row[1] + infraction_gain
        new_legacy_xp = row[2] + max(0, xp_gain) 
        
        cursor.execute("""
            UPDATE users 
            SET xp = ?, 
                infractions = ?, 
                legacy_xp = ?, 
                username = ? 
            WHERE user_id = ?
        """, (new_xp, new_infractions, new_legacy_xp, username, user_id))

    conn.commit()
    conn.close()

def calculate_stats(user_id):
    """Calculates the alignment, rank, and trust score for a user."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT xp, infractions, legacy_xp FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()
    
    if not data:
        return "Hero", "F-Class Rookie", 100, 0, 0, 0

    xp, infractions, legacy_xp = data[0], data[1], data[2]
    
    # Primary Score: XP - (Infractions * Penalty)
    effective_score = xp - (infractions * INFRACTION_VALUE)
    
    # Determine Alignment and Rank
    ranks = HERO_RANKS if effective_score >= 0 else VILLAIN_RANKS
    
    # Rank is based on the magnitude of the effective score
    score_to_rank = effective_score if effective_score >= 0 else abs(effective_score)

    rank_title = max(
        (title for threshold, title in ranks.items() if score_to_rank >= threshold),
        default=ranks[0]
    )
    
    alignment = "Hero 🦸" if effective_score >= 0 else "Villain 😈"
    trust_score = max(0, 100 - (infractions * TRUST_LOSS_PER_INFRACTION))
    
    return alignment, rank_title, trust_score, xp, infractions, legacy_xp

def get_leaderboard(limit=10):
    """Fetches the top users based on their effective score."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, xp, infractions FROM users")
    all_users = cursor.fetchall()
    conn.close()
    
    ranked_users = []
    for user_id, username, xp, infractions in all_users:
        effective_score = xp - (infractions * INFRACTION_VALUE)
        
        # Calculate rank and alignment for display
        if effective_score >= 0:
            ranks = HERO_RANKS
        else:
            ranks = VILLAIN_RANKS
            
        score_to_rank = effective_score if effective_score >= 0 else abs(effective_score)
        rank_title = max(
            (title for threshold, title in ranks.items() if score_to_rank >= threshold),
            default=ranks[0]
        )
        
        ranked_users.append({
            'username': username, 
            'xp': xp, 
            'effective_score': effective_score,
            'rank_title': rank_title,
        })
        
    # Sort by effective score descending
    ranked_users.sort(key=lambda x: x['effective_score'], reverse=True)
    
    return ranked_users[:limit]


# --- 3. HELPER FUNCTIONS ---

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user is an admin or owner in the group."""
    if update.effective_chat.type not in ["group", "supergroup"]:
        return False
        
    user_status = await context.bot.get_chat_member(
        chat_id=update.effective_chat.id, 
        user_id=update.effective_user.id
    )
    # FIX: Use ChatMemberStatus.OWNER for compatibility
    return user_status.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]


# --- 4. COMMAND HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    await update.message.reply_text(
        f"🔥 **Welcome to Rankify!** 🔥\n\n"
        f"I track group activity, reward heroes, and punish villains.\n\n"
        f"Use **/help** to see all commands!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The /help command showing all available commands."""
    help_text = (
        "📜 **Rankify Command Guide** 📜\n"
        "-------------------------------------\n"
        "**👤 General Commands**\n"
        "🔥 **/aura** - View your personal rank card: Rank, XP, Trust Score, and Infractions.\n"
        "🏆 **/legends** - Display the group's Top 10 members based on overall standing.\n"
        "❓ **/help** - Shows this command list.\n"
        "-------------------------------------\n"
        "**🛡️ Admin & Owner Commands**\n"
        "💀 **/punish** `(Reply)` - Manually gives 1 Infraction and -100 XP to a user. Used to discipline bad behavior.\n"
        "📢 **/send** `[message]` - Deletes your command and sends the message as a clean announcement from the bot.\n"
        "🔄 **/rebirth** - *[Future]* Resets all XP to start a new season (Legacy XP remains).\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def aura_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /aura command to show user profile/rank."""
    user = update.effective_user
    alignment, rank_title, trust_score, xp, infractions, legacy_xp = calculate_stats(user.id)
    
    alignment_emoji = "🦸" if alignment.startswith("Hero") else "😈"
    
    reply_text = (
        f"**{alignment_emoji} {user.first_name}'s AURA**\n"
        f"----------------------------------------\n"
        f"**Rank:** {rank_title}\n"
        f"**Alignment:** {alignment}\n"
        f"**Current XP:** {xp} (Legacy: {legacy_xp})\n"
        f"**Trust Score:** {trust_score}%\n"
        f"**Infractions:** {infractions}\n"
        f"----------------------------------------\n"
        f"A true **{rank_title}**! Keep climbing the ranks! 📈"
    )
    
    await update.message.reply_text(reply_text, parse_mode="Markdown")

async def legends_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /legends command to show the leaderboard."""
    leaderboard = get_leaderboard(limit=10)
    
    if not leaderboard:
        await update.message.reply_text("🏆 The Great Hall of Heroes is empty! Start chatting to earn XP!")
        return
        
    header = "🏆 **THE GREAT HALL OF HEROES** 🏆\n"
    leaderboard_text = ""
    
    for i, user_data in enumerate(leaderboard):
        username = user_data['username']
        rank_title = user_data['rank_title']
        xp = user_data['xp']
        effective_score = user_data['effective_score']
        
        # Determine the medal emoji
        if i == 0: medal = "🥇"
        elif i == 1: medal = "🥈"
        elif i == 2: medal = "🥉"
        else: medal = f"{i+1}."
            
        color = "🟢" if effective_score >= 0 else "🔴"
        
        leaderboard_text += (
            f"{medal} **{username}**\n"
            f"   → {color} Rank: *{rank_title}* (XP: {xp})\n"
        )
        
    await update.message.reply_text(header + leaderboard_text, parse_mode="Markdown")


async def punish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to manually give an infraction to a user."""
    
    if not await is_admin(update, context):
        await update.message.reply_text("🚫 You must be an admin to use the **/punish** command.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Usage: Please **reply to the user's message** you wish to punish.")
        return

    target_user = update.message.reply_to_message.from_user
    target_user_id = target_user.id
    target_username = target_user.first_name

    update_user_xp(target_user_id, target_username, xp_gain=INFRACTION_PENALTY_XP, infraction_gain=1)
    
    _, new_rank_title, new_trust_score, _, new_infractions, _ = calculate_stats(target_user_id)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"💀 **{target_username}** has been judged and penalized!\n"
            f"**Penalty:** 1 Infraction, {INFRACTION_PENALTY_XP} XP loss.\n"
            f"**Current Status:** Rank: {new_rank_title} | Trust: {new_trust_score}%"
        ),
        parse_mode="Markdown"
    )

async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to delete the command and send the message as the bot."""
    
    if not await is_admin(update, context):
        await update.message.reply_text("🚫 Only admins can use the **/send** command.")
        return
        
    message = update.message
    
    if not context.args:
        await message.reply_text("❌ Usage: `/send Your message here`")
        return
        
    text_to_send = message.text.split(" ", 1)[1]
    
    await context.bot.send_message(
        chat_id=message.chat_id,
        text=text_to_send,
        parse_mode="Markdown" 
    )

    try:
        await message.delete()
    except Exception as e:
        print(f"Error deleting admin message: {e}") 


# --- 5. ACTIVITY TRACKER ---

async def track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Listens to all messages to update XP and check for banned words."""
    
    if (update.effective_chat.type in ["group", "supergroup"] 
        and update.message.text
        and not update.effective_user.is_bot):
        
        user = update.effective_user
        username = user.first_name
        message_text = update.message.text.lower()
        
        # --- Banned Word Check ---
        found_infraction = False
        for word in BANNED_WORDS:
            # Check for the whole word using regex
            if re.search(r'\b' + re.escape(word) + r'\b', message_text):
                found_infraction = True
                break

        if found_infraction:
            update_user_xp(user.id, username, xp_gain=INFRACTION_PENALTY_XP, infraction_gain=1)
            try:
                await update.message.delete()
            except:
                pass 
            
            _, new_rank_title, new_trust_score, _, new_infractions, _ = calculate_stats(user.id)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"🚫 **{username}**'s message was corrupted! **(-1 Infraction)**.\n"
                    f"💀 Status: Rank: {new_rank_title} | Trust: {new_trust_score}%"
                ),
                parse_mode="Markdown"
            )
            return

        # --- Regular XP Gain & Rank Up/Down Check ---
        
        old_alignment, old_rank_title, _, _, _, _ = calculate_stats(user.id)
        update_user_xp(user.id, username, xp_gain=XP_PER_MESSAGE)
        new_alignment, new_rank_title, _, new_xp, new_infractions, _ = calculate_stats(user.id)

        # Notify only on Rank Title Change (e.g., Rookie -> Apprentice)
        if old_rank_title != new_rank_title:
            if new_alignment.startswith("Hero"):
                await update.message.reply_text(
                    f"💥 **{username}** has ascended to **{new_rank_title}**!\n"
                    f"⚡ The group trembles before their power! (XP: {new_xp})",
                    parse_mode="Markdown"
                )
            else:
                 await update.message.reply_text(
                    f"💀 **{username}** has fallen to **{new_rank_title}**!\n"
                    f"They disgrace the group with their presence. (Infractions: {new_infractions})",
                    parse_mode="Markdown"
                )


# --- 6. MAIN EXECUTION ---

def main():
    """Starts the bot."""
    
    if not BOT_TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN not found. Make sure .env is in the same folder and correctly formatted.")
        return

    init_db()
    print("Database initialized successfully.")
    
    application = Application.builder().token(BOT_TOKEN).build()
    print("Bot application built successfully.")

    # Register Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command)) # Added /help
    application.add_handler(CommandHandler("aura", aura_command))
    application.add_handler(CommandHandler("legends", legends_command)) # Added /legends
    application.add_handler(CommandHandler("punish", punish_command, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("send", send_command, filters=filters.ChatType.GROUPS)) 
    
    # Register Message Handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, track_activity))
    
    print("Starting bot polling... (Press Ctrl+C to stop)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()