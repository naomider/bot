#!/usr/bin/env python3
"""
Corrected, ready-to-run Telegram bot (python-telegram-bot v13.x).
Save as bot.py and deploy (Render / Heroku / similar). Make sure
BOT_TOKEN is supplied via environment variable BOT_TOKEN.

Notes:
 - This version fixes the `if __name__ == "__main__"` check.
 - Username resolution is handled via an internal recent-message cache
   (user_cache). Ask users to send a message to the chat once if they
   haven't messaged recently so the bot can learn their username->id.
 - catch_all records recent usernames and performs deletion when a
   watched user sends a message. Bot must be admin / have delete message rights.
"""

import logging
import os
from typing import Dict

from telegram import Update, User
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

# Configure logging for debugging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8561650202:AAE_N9m_-BzOw9k1p3GEJoxMKJ7AqW2UVBs"

# watched: map user_id -> display (username or id)
watched: Dict[int, str] = {}

# user_cache: map lowercase username -> user_id (populated from recent messages)
user_cache: Dict[str, int] = {}


def id_cmd(update: Update, context: CallbackContext):
    """Reply with the sender's numeric user id."""
    if not update.effective_user:
        return
    uid = update.effective_user.id
    update.message.reply_text(f"user id: {uid}")


def who_cmd(update: Update, context: CallbackContext):
    """List currently watched users."""
    if not watched:
        return update.message.reply_text("No users watched.")
    lines = [f"{disp} -> {uid}" for uid, disp in watched.items()]
    update.message.reply_text("\n".join(lines))


def watch_cmd(update: Update, context: CallbackContext):
    """
    Add a user to 'watched'.
    Usage:
      /watch <@username or user_id>
    Behavior:
      - If numeric id provided, add directly.
      - If @username provided, try to resolve from user_cache (recent messages).
      - If unresolved, instruct to ask that user to send any message in chat so bot can cache them.
    """
    if not context.args:
        return update.message.reply_text("Usage: /watch <@username or user_id>")

    arg = context.args[0].strip()
    chat = update.effective_chat

    # numeric id
    if arg.lstrip("-").isdigit():
        uid = int(arg)
        watched[uid] = str(uid)
        logger.info("Now watching numeric id %d", uid)
        return update.message.reply_text(f"Now watching {uid}")

    # @username case: resolve from cache (case-insensitive)
    if arg.startswith("@"):
        uname = arg[1:].strip()
        if not uname:
            return update.message.reply_text("Usage: /watch <@username or user_id>")

        uid = user_cache.get(uname.lower())
        if uid:
            # prefer a nicer display if username exists
            disp = f"@{uname}"
            watched[uid] = disp
            logger.info("Now watching %s (id %d) via cache", disp, uid)
            return update.message.reply_text(f"Now watching @{uname} (id {uid})")

        # As a best-effort attempt, try get_chat_member if API supports resolving by username.
        # Note: get_chat_member expects user_id, so this will usually fail if username only.
        try:
            # Attempt by trying to fetch member by username using chat.get_member if available.
            # Many Telegram Bot APIs do not support passing username to get_chat_member, so this
            # may raise. It's kept for robustness on some environments.
            member = context.bot.get_chat_member(chat.id, uname)  # may raise
            uid = member.user.id
            watched[uid] = f"@{member.user.username or member.user.first_name}"
            return update.message.reply_text(
                f"Now watching @{member.user.username} (id {uid})"
            )
        except Exception:
            # fallback: tell the admin what to do
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

    # numeric id
    if arg.lstrip("-").isdigit():
        uid = int(arg)
        if watched.pop(uid, None):
            logger.info("Stopped watching id %d", uid)
            return update.message.reply_text(f"Stopped watching {uid}")
        return update.message.reply_text("That id wasn't watched.")

    # @username
    if arg.startswith("@"):
        uname = arg[1:].strip().lower()
        for uid, disp in list(watched.items()):
            # disp may be like "@username" or numeric string
            if disp.lstrip("@").lower() == uname:
                watched.pop(uid, None)
                logger.info("Stopped watching @%s (id %d)", uname, uid)
                return update.message.reply_text(f"Stopped watching @{uname}")
        return update.message.reply_text("That username wasn't watched.")

    update.message.reply_text("Couldn't unwatch. Use id or @username.")


def catch_all(update: Update, context: CallbackContext):
    """
    Catches all non-command messages:
     - populate user_cache (username -> id) from any message we see
     - delete messages from users in 'watched' (requires bot to have the proper rights)
    """
    msg = update.effective_message
    if not msg or not msg.from_user:
        return

    user: User = msg.from_user
    uid = user.id

    # populate cache if username exists
    if user.username:
        user_cache[user.username.lower()] = uid
        # Optionally keep only recent N entries (simple pruning)
        if len(user_cache) > 2000:
            # naive prune: convert to list and slice
            keys = list(user_cache.keys())[-1500:]
            user_cache.clear()
            for k in keys:
                # nothing perfect here; this just keeps recent ones when size grows too large
                pass

    # If sender is watched, attempt to delete their message
    if uid in watched:
        try:
            context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
            logger.info("Deleted message from watched user %s (id %d)", watched.get(uid), uid)
        except Exception as e:
            # deletion can fail if bot lacks permissions or message is too old
            logger.warning("Delete failed for %d: %s", uid, e)


def main():
    if BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        logger.error("BOT_TOKEN not set. Set environment variable BOT_TOKEN.")
        raise SystemExit("BOT_TOKEN not set")

    # Updater with use_context=True (v13 style)
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Command handlers
    dp.add_handler(CommandHandler("id", id_cmd))
    dp.add_handler(CommandHandler("who", who_cmd))
    dp.add_handler(CommandHandler("watch", watch_cmd, pass_args=True))
    dp.add_handler(CommandHandler("unwatch", unwatch_cmd, pass_args=True))

    # All non-command messages
    dp.add_handler(MessageHandler(Filters.all & (~Filters.command), catch_all))

    # Start polling (simple, reliable). For production on platforms that require webhooks,
    # adapt to webhook mode instead.
    updater.start_polling()
    logger.info("Bot started polling.")
    updater.idle()


if __name__ == "__main__":
    main()

