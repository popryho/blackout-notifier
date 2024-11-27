import argparse
import logging
from datetime import datetime, timedelta, timezone

import requests

from db_utils import DBUtils

logging.basicConfig(level=logging.INFO)


def format_duration(duration: timedelta) -> str:
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes = remainder // 60
    return f"{hours} год. {minutes} хв."


def send_telegram_message(bot_token: str, chat_id: int, message: str):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        response = requests.post(url, data=data)
        if response.status_code != 200:
            logging.error(f"Failed to send message: {response.text}")
    except Exception as e:
        logging.error(f"Exception when sending message: {e}")


def get_time_range_for_yesterday() -> tuple:
    now_utc = datetime.now(timezone.utc)
    yesterday = now_utc - timedelta(days=1)
    start_of_day = datetime.combine(
        yesterday.date(), datetime.min.time(), tzinfo=timezone.utc)
    end_of_day = datetime.combine(
        yesterday.date(), datetime.max.time(), tzinfo=timezone.utc)
    return start_of_day, end_of_day


def build_message(date_str: str, total_on_time: timedelta, total_off_time: timedelta) -> str:
    message_header = f"💡Статистика за вчора ({date_str}):\n"

    if total_off_time == timedelta():
        message_body = "\n🥳Електрика була увесь день!"
    elif total_on_time == timedelta():
        message_body = "\n😞Електрика була відсутня весь день."
    else:
        total_on_str = format_duration(total_on_time)
        total_off_str = format_duration(total_off_time)
        message_body = (
            f"\n🟢Електрика присутня: {total_on_str}.\n"
            f"🔴Електрика відсутня: {total_off_str}."
        )

    return message_header + message_body


def send_daily_statistics(bot_token: str, chat_id: int, db_file: str):
    db = DBUtils(db_file)
    start_of_day, end_of_day = get_time_range_for_yesterday()
    date_str = (start_of_day).strftime('%Y-%m-%d')

    total_on_time, total_off_time = calculate_total_times(
        db, start_of_day, end_of_day)
    message = build_message(date_str, total_on_time, total_off_time)
    send_telegram_message(bot_token, chat_id, message)


def calculate_total_times(db: DBUtils, start_time: datetime, end_time: datetime):
    rows = db.get_status_changes(start_time, end_time)
    previous_status = db.get_last_status_before(start_time)
    total_on_time = timedelta()
    total_off_time = timedelta()
    previous_time = start_time

    if not rows:
        duration = end_time - start_time
        if previous_status:
            total_on_time = duration
        else:
            total_off_time = duration
        return total_on_time, total_off_time

    for status, time_str in rows:
        current_time = datetime.fromisoformat(time_str)
        duration = current_time - previous_time
        if previous_status:
            total_on_time += duration
        else:
            total_off_time += duration
        previous_time = current_time
        previous_status = status

    # Add the remaining time until the end of the day
    duration = end_time - previous_time
    if previous_status:
        total_on_time += duration
    else:
        total_off_time += duration

    return total_on_time, total_off_time


def main():
    parser = argparse.ArgumentParser(
        description="Send daily electricity statistics.")
    parser.add_argument("--token", required=True,
                        help="Telegram Bot API token")
    parser.add_argument("--chat-id", required=True,
                        type=int, help="Telegram Chat ID")
    parser.add_argument("--db-file", default="state.db",
                        help="SQLite database file")
    args = parser.parse_args()

    send_daily_statistics(args.token, args.chat_id, args.db_file)


if __name__ == "__main__":
    main()
