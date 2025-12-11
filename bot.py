#!/usr/bin/env python3
"""
Webhook version of the Telegram bot (python-telegram-bot v13.x).
Deploy on platforms that support webhooks (Render / Heroku / Railway / etc.).
Make sure to set all required environment variables.

Required environment variables:
- BOT_TOKEN: Your bot token from BotFather
- WEBHOOK_URL: The public URL where your app is hosted (with HTTPS)
- PORT: Port to listen on (optional, defaults to 8080)
- WEBHOOK_PATH: Path for webhook endpoint (optional, defaults to '/webhook')

Features:
- Webhook-based for better performance in production
- Persistent storage using sqlite3 (saves watched users across restarts)
- Health check endpoint for monitoring
- Proper webhook setup and cleanup
"""

import logging
import os
import sqlite3
from typing import Dict, Optional
from threading import Lock

from telegram import Update, User
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    Dispatcher,
)
from flask import Flask, request, jsonify

# Configure logging for debugging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8561650202:AAE_N9m_-BzOw9k1p3GEJoxMKJ7AqW2UVBs"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")
DB_PATH = os.environ.get("DB_PATH", "bot_data.db")

# Flask app for webhook server
app = Flask(__name__)

# Global variables
updater: Optional[Updater] = None
bot_data_initialized = False
db_lock = Lock()

# Database setup
def init_database():
    """Initialize the SQLite database for persistent storage."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        
        # Create tables if they don't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watched_users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_cache (
                username TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    logger.info("Database initialized")

def load_watched_users() -> Dict[int, str]:
    """Load watched users from database."""
    watched = {}
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, display_name FROM watched_users")
            rows = cursor.fetchall()
            conn.close()
            
            for user_id, display_name in rows:
                watched[user_id] = display_name
    except Exception as e:
        logger.error(f"Error loading watched users: {e}")
    return watched

def save_watched_user(user_id: int, display_name: str):
    """Save or update a watched user in database."""
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO watched_users (user_id, display_name)
                VALUES (?, ?)
            ''', (user_id, display_name))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error saving watched user: {e}")

def remove_watched_user(user_id: int):
    """Remove a watched user from database."""
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM watched_users WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error removing watched user: {e}")

def save_user_cache(username: str, user_id: int):
    """Save or update user cache entry."""
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO user_cache (username, user_id, last_seen)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (username.lower(), user_id))
            
            # Clean up old entries (keep only last 2000)
            cursor.execute("DELETE FROM user_cache WHERE rowid NOT IN (SELECT rowid FROM user_cache ORDER BY last_seen DESC LIMIT 2000)")
            
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error saving user cache: {e}")

def load_user_cache() -> Dict[str, int]:
    """Load user cache from database."""
    user_cache = {}
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT username, user_id FROM user_cache")
            rows = cursor.fetchall()
            conn.close()
            
            for username, user_id in rows:
                user_cache[username] = user_id
    except Exception as e:
        logger.error(f"Error loading user cache: {e}")
    return user_cache

def id_cmd(update: Update, context: CallbackContext):
    """Reply with the sender's numeric user id."""
    if not update.effective_user:
        return
    uid = update.effective_user.id
    update.message.reply_text(f"user id: {uid}")


def who_cmd(update: Update, context: CallbackContext):
    """List currently watched users."""
    watched = context.bot_data.get('watched', {})
    if not watched:
        return update.message.reply_text("No users watched.")
    lines = [f"{disp} -> {uid}" for uid, disp in watched.items()]
    update.message.reply_text("\n".join(lines))


def watch_cmd(update: Update, context: CallbackContext):
    """
    Add a user to 'watched'.
    Usage:
      /watch <@username or user_id>
    """
    if not context.args:
        return update.message.reply_text("Usage: /watch <@username or user_id>")

    arg = context.args[0].strip()
    chat = update.effective_chat
    watched = context.bot_data.setdefault('watched', {})
    user_cache = context.bot_data.setdefault('user_cache', {})

    # numeric id
    if arg.lstrip("-").isdigit():
        uid = int(arg)
        watched[uid] = str(uid)
        save_watched_user(uid, str(uid))
        logger.info("Now watching numeric id %d", uid)
        return update.message.reply_text(f"Now watching {uid}")

    # @username case
    if arg.startswith("@"):
        uname = arg[1:].strip()
        if not uname:
            return update.message.reply_text("Usage: /watch <@username or user_id>")

        uid = user_cache.get(uname.lower())
        if uid:
            disp = f"@{uname}"
            watched[uid] = disp
            save_watched_user(uid, disp)
            logger.info("Now watching %s (id %d) via cache", disp, uid)
            return update.message.reply_text(f"Now watching @{uname} (id {uid})")

        # Try to get chat member by username
        try:
            member = context.bot.get_chat_member(chat.id, uname)
            uid = member.user.id
            disp = f"@{member.user.username or member.user.first_name}"
            watched[uid] = disp
            save_watched_user(uid, disp)
            return update.message.reply_text(
                f"Now watching @{member.user.username} (id {uid})"
            )
        except Exception as e:
            logger.warning(f"Could not resolve username @{uname}: {e}")
            return update.message.reply_text(
                "Couldn't resolve that username. Ask them to send *any* message in this chat "
                "so I can see them, then run /watch @username again."
            )

    update.message.reply_text("Invalid argument. Use @username or user_id.")


def unwatch_cmd(update: Update, context: CallbackContext):
    """Stop watching a user by id or @username."""
    if not context.args:
        return update.message.reply_text("Usage: /unwatch <@username or user_id>")

    arg = context.args[0].strip()
    watched = context.bot_data.setdefault('watched', {})

    # numeric id
    if arg.lstrip("-").isdigit():
        uid = int(arg)
        if watched.pop(uid, None):
            remove_watched_user(uid)
            logger.info("Stopped watching id %d", uid)
            return update.message.reply_text(f"Stopped watching {uid}")
        return update.message.reply_text("That id wasn't watched.")

    # @username
    if arg.startswith("@"):
        uname = arg[1:].strip().lower()
        for uid, disp in list(watched.items()):
            if disp.lstrip("@").lower() == uname:
                watched.pop(uid, None)
                remove_watched_user(uid)
                logger.info("Stopped watching @%s (id %d)", uname, uid)
                return update.message.reply_text(f"Stopped watching @{uname}")
        return update.message.reply_text("That username wasn't watched.")

    update.message.reply_text("Couldn't unwatch. Use id or @username.")


def catch_all(update: Update, context: CallbackContext):
    """
    Catches all non-command messages:
     - populate user_cache (username -> id) from any message we see
     - delete messages from users in 'watched'
    """
    msg = update.effective_message
    if not msg or not msg.from_user:
        return

    user: User = msg.from_user
    uid = user.id

    # populate cache if username exists
    if user.username:
        user_cache = context.bot_data.setdefault('user_cache', {})
        user_cache[user.username.lower()] = uid
        save_user_cache(user.username, uid)

    # If sender is watched, attempt to delete their message
    watched = context.bot_data.setdefault('watched', {})
    if uid in watched:
        try:
            context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
            logger.info("Deleted message from watched user %s (id %d)", watched.get(uid), uid)
        except Exception as e:
            logger.warning("Delete failed for %d: %s", uid, e)


@app.route('/')
def index():
    """Health check endpoint."""
    return jsonify({
        "status": "online",
        "service": "telegram-bot",
        "webhook_set": updater is not None and updater.bot is not None
    })


@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    """Telegram webhook endpoint."""
    if updater is None:
        return jsonify({"status": "error", "message": "Bot not initialized"}), 500
    
    # Process the update
    update = Update.de_json(request.get_json(force=True), updater.bot)
    updater.dispatcher.process_update(update)
    return jsonify({"status": "ok"})


@app.route('/set_webhook', methods=['POST'])
def set_webhook_endpoint():
    """Manually set webhook (useful for debugging)."""
    if not WEBHOOK_URL:
        return jsonify({"status": "error", "message": "WEBHOOK_URL not set"}), 400
    
    try:
        webhook

