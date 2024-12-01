# utils.py

from datetime import datetime, timedelta

import requests
from loguru import logger

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, UTC_PLUS_2


def send_telegram_message(message: str, parse_mode: str = None) -> None:
    """Send a message via Telegram Bot API with night-hour silent mode."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    current_hour = datetime.now(UTC_PLUS_2).hour
    if 23 <= current_hour or current_hour < 7:
        data["disable_notification"] = True

    if parse_mode:
        data["parse_mode"] = parse_mode

    try:
        response = requests.post(url, data=data)
        response.raise_for_status()
        logger.info("Telegram message sent successfully.")
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")


def format_duration(duration: timedelta) -> str:
    """Format a timedelta into a readable string."""
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} год.")
    if minutes > 0 or hours == 0:
        parts.append(f"{minutes} хв.")
    return " ".join(parts)


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text
