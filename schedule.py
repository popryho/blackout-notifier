# schedule.py

import time
from datetime import datetime, timedelta
from typing import Dict, List

import requests
from loguru import logger

from config import CHECK_INTERVAL, GROUP_ID, UTC_PLUS_2
from db import outage_schedule_init, outage_schedule_outdated, outage_schedule_update
from tg import escape_markdown_v2, format_duration, send_telegram_message


def fetch_schedule() -> List[Dict]:
    """Fetch the schedule from the API and return processed data."""
    url = "https://api.yasno.com.ua/api/v1/pages/home/schedule-turn-off-electricity"
    current_time = datetime.now(UTC_PLUS_2)

    if current_time.time() < (datetime.min + timedelta(minutes=5)).time():
        logger.info("Skipping schedule fetching due to the time of the day.")
        time.sleep(300)
    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return []

    group_number = GROUP_ID
    schedules = []

    for day_label, days_ahead in [("today", 0), ("tomorrow", 1)]:
        date = current_time.date() + timedelta(days=days_ahead)
        schedule_data = extract_schedule_data(data, day_label, group_number)
        if schedule_data:
            schedules.extend(process_schedule(schedule_data, date))
        else:
            logger.warning(f"{day_label.capitalize()}'s outage schedule is empty.")

    return schedules


def extract_schedule_data(data: Dict, day_label: str, group_number: int) -> List[Dict]:
    """Extract schedule data for a specific day."""
    try:
        return data["components"][4]["dailySchedule"]["kiev"][day_label]["groups"][
            group_number
        ]
    except (KeyError, IndexError):
        logger.warning(f"{day_label.capitalize()}'s schedule is not available.")
        return []


def process_schedule(schedule_data: List[Dict], date: datetime.date) -> List[Dict]:
    """Process schedule data and include only future outages."""
    schedule = []
    for interval in schedule_data:
        if interval.get("type") != "DEFINITE_OUTAGE":
            continue

        start_hour = interval["start"]
        time_slot = datetime(
            date.year, date.month, date.day, start_hour, tzinfo=UTC_PLUS_2
        )
        if time_slot < datetime.now(UTC_PLUS_2):
            continue

        schedule.append(
            {
                "start": time_slot,
                "end": time_slot + timedelta(hours=1),
            }
        )
    return schedule


def group_and_merge_intervals(intervals: List[Dict]) -> Dict[datetime.date, List[Dict]]:
    """Group intervals by date and merge consecutive intervals."""
    intervals.sort(key=lambda x: x["start"])
    grouped = {}

    for interval in intervals:
        date_key = interval["start"].date()
        grouped.setdefault(date_key, [])
        day_intervals = grouped[date_key]

        if day_intervals and interval["start"] == day_intervals[-1]["end"]:
            day_intervals[-1]["end"] = interval["end"]
        else:
            day_intervals.append(interval)

    return grouped


def build_message(intervals: List[Dict]) -> str:
    """Construct a Telegram message based on intervals."""
    current_time_str = datetime.now(UTC_PLUS_2).strftime('%d.%m.%Y %H:%M')
    header = (
        f"🗓️ Графік відключень, {GROUP_ID} група\n"
        f"🔄 Оновлено: {escape_markdown_v2(current_time_str)}"
    )
    message_lines = [header]

    if not intervals:
        message_lines.append("▪️ Наразі незаплановано")
        return "\n".join(message_lines)

    grouped_intervals = group_and_merge_intervals(intervals)

    for date, intervals in sorted(grouped_intervals.items()):
        date_str = date.strftime("на *%d\\.%m\\.%Y*")
        message_lines.append(f"\n{date_str}")
        for interval in intervals:
            start_str = interval["start"].strftime("%H:%M")
            end_str = interval["end"].strftime("%H:%M")
            duration_str = format_duration(interval["end"] - interval["start"])
            line = f"▪️ {start_str} - {end_str}  [{duration_str}]"
            message_lines.append(escape_markdown_v2(line))

    return "\n".join(message_lines)


def update_and_notify():
    """Fetch schedule, update database, and send notifications."""
    schedule_entries = fetch_schedule()

    formatted_schedule_entries = [(entry["start"],) for entry in schedule_entries]

    if outage_schedule_outdated(formatted_schedule_entries):
        logger.info("Schedule update detected. Updating the database.")
        outage_schedule_update(formatted_schedule_entries)

        message = build_message(schedule_entries)
        if message:
            send_telegram_message(message, parse_mode="MarkdownV2")
        logger.info("Schedule updated and message sent.")
    else:
        logger.info("No new schedule data available.")


def main():
    """Main function to initialize and periodically fetch schedule."""
    outage_schedule_init()

    while True:
        update_and_notify()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
