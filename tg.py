# utils.py

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from loguru import logger

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")


def send_telegram_message(message: str, parse_mode: str = None) -> None:
    """Send a message via Telegram Bot API with night-hour silent mode."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    current_hour = datetime.now(KYIV_TIMEZONE).hour
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


def send_telegram_image(
    image_path: str, caption: str = None, parse_mode: str = None
) -> None:
    """Send an image to a Telegram chat via the Bot API with optional caption and silent mode during night hours."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {"chat_id": TELEGRAM_CHAT_ID}

    # Night-hour silent mode
    current_hour = datetime.now(KYIV_TIMEZONE).hour
    if 23 <= current_hour or current_hour < 7:
        data["disable_notification"] = True

    if caption:
        data["caption"] = caption
    if parse_mode:
        data["parse_mode"] = parse_mode

    try:
        with open(image_path, "rb") as photo_file:
            files = {"photo": photo_file}
            response = requests.post(url, data=data, files=files)
            response.raise_for_status()
            logger.info("Telegram image sent successfully.")
    except FileNotFoundError:
        logger.error(f"Image file not found: {image_path}")
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram image: {e}")
