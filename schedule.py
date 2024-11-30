# schedule.py

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import requests
from loguru import logger

from config import CHECK_INTERVAL, GROUP_ID, UTC_PLUS_2
from db import outage_schedule_init, outage_schedule_outdated, outage_schedule_update
from tg import escape_markdown_v2, format_duration, send_telegram_message


def fetch_schedule() -> Tuple[List[Dict], datetime]:
    """Fetch the schedule from the API and return processed data."""
    url = "https://api.yasno.com.ua/api/v1/pages/home/schedule-turn-off-electricity"
    current_time = datetime.now(UTC_PLUS_2)

    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return [], datetime.min.replace(tzinfo=UTC_PLUS_2)

    group_number = GROUP_ID
    schedules = []

    for day_label, days_ahead in [("today", 0), ("tomorrow", 1)]:
        date = current_time.date() + timedelta(days=days_ahead)
        schedule_data = extract_schedule_data(data, day_label, group_number)
        if schedule_data:
            schedules.extend(process_schedule(schedule_data, date))
        else:
            logger.warning(f"{day_label.capitalize()}'s schedule is not available.")

    registry_update_time = extract_registry_update_time(data)
    return schedules, registry_update_time


def extract_schedule_data(data: Dict, day_label: str, group_number: int) -> List[Dict]:
    """Extract schedule data for a specific day."""
    try:
        return data["components"][4]["dailySchedule"]["kiev"][day_label]["groups"][
            group_number
        ]
    except (KeyError, IndexError):
        return []


def extract_registry_update_time(data: Dict) -> datetime:
    """Extract the last registry update time."""
    try:
        timestamp = data["components"][4]["lastRegistryUpdateTime"]
        return datetime.fromtimestamp(timestamp, UTC_PLUS_2)
    except (KeyError, ValueError):
        return datetime.min.replace(tzinfo=UTC_PLUS_2)


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


def build_message(intervals: List[Dict], registry_update_time: datetime) -> str:
    """Construct a Telegram message based on intervals."""
    if not intervals:
        return ""

    grouped_intervals = group_and_merge_intervals(intervals)
    header = (
        f"🗓️ Графік відключень, {GROUP_ID} група\n"
        f"🔄 Оновлено: {escape_markdown_v2(registry_update_time.strftime('%d.%m.%Y %H:%M'))}"
    )
    message_lines = [header]

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
    schedule_entries, registry_update_time = fetch_schedule()

    if registry_update_time == datetime.min.replace(tzinfo=UTC_PLUS_2):
        # Failed to fetch schedule
        return

    if outage_schedule_outdated(registry_update_time):
        logger.info("Schedule update detected. Updating the database.")
        schedule_data = [
            (entry["start"], registry_update_time) for entry in schedule_entries
        ]
        outage_schedule_update(schedule_data)

        message = build_message(schedule_entries, registry_update_time)
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
