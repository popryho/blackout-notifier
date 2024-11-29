# schedule.py

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import requests
from loguru import logger

from config import CHECK_INTERVAL, GROUP_ID, UTC_PLUS_2
from db import (check_schedule_updated, init_outage_schedule_table,
                update_outage_schedule)
from tg import format_duration, send_telegram_message


def fetch_schedule() -> Tuple[List[Dict], datetime]:
    """Fetch and process the schedule from the API."""
    current_time = datetime.now(UTC_PLUS_2)
    url = "https://api.yasno.com.ua/api/v1/pages/home/schedule-turn-off-electricity"
    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return [], datetime.min.replace(tzinfo=UTC_PLUS_2)

    group_number = GROUP_ID
    schedules = []

    for day_label, days_ahead in [('today', 0), ('tomorrow', 1)]:
        date = current_time.date() + timedelta(days=days_ahead)
        try:
            schedule_data = data['components'][4]['dailySchedule']['kiev'][day_label]['groups'][group_number]
            schedules.extend(process_schedule(schedule_data, date))
        except (KeyError, IndexError):
            logger.warning(
                f"{day_label.capitalize()}'s schedule is not available.")

    registry_update_timestamp = data['components'][4]['lastRegistryUpdateTime']
    registry_update_time = datetime.fromtimestamp(
        registry_update_timestamp, UTC_PLUS_2)

    return schedules, registry_update_time


def process_schedule(schedule_data, date) -> List[Dict]:
    """Process schedule data for a specific date, only including outages."""
    schedule = []
    for interval in schedule_data:
        if interval['type'] != 'DEFINITE_OUTAGE':
            continue  # Only include outages
        start_hour = interval['start']
        time_slot = datetime(date.year, date.month, date.day,
                             start_hour, tzinfo=UTC_PLUS_2)
        if time_slot < datetime.now(UTC_PLUS_2):
            continue
        schedule.append({
            'start': time_slot,
            'end': time_slot + timedelta(hours=1)
        })
    return schedule


def group_and_merge_intervals_by_date(intervals: List[Dict]) -> Dict[datetime.date, List[Dict]]:
    """Group intervals by date and merge consecutive intervals."""
    intervals.sort(key=lambda x: x['start'])
    grouped = {}
    for interval in intervals:
        date_key = interval['start'].date()
        grouped.setdefault(date_key, [])
        day_intervals = grouped[date_key]
        if day_intervals and interval['start'] == day_intervals[-1]['end']:
            day_intervals[-1]['end'] = interval['end']
        else:
            day_intervals.append(interval)
    return grouped


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


def build_message(intervals: List[Dict], registry_update_time: datetime) -> str:
    """Build the Telegram message based on grouped and merged intervals."""
    if not intervals:
        return ""

    grouped_intervals = group_and_merge_intervals_by_date(intervals)

    header = (
        f"🗓️ Графік відключень, {GROUP_ID} група\n"
        f"🔄 Оновлено: {escape_markdown_v2(
            registry_update_time.strftime('%d.%m.%Y %H:%M'))}"
    )
    message_lines = [header]

    for date in sorted(grouped_intervals.keys()):
        date_str = date.strftime("на *%d\\.%m\\.%Y*")
        message_lines.append(f"\n{date_str}")
        for interval in grouped_intervals[date]:
            start_str = interval['start'].strftime("%H:%M")
            end_str = interval['end'].strftime("%H:%M")
            duration = interval['end'] - interval['start']
            duration_str = format_duration(duration)
            line = f"▪️ {start_str} - {end_str}  [{duration_str}]"
            message_lines.append(escape_markdown_v2(line))

    return "\n".join(message_lines)


def main():
    """Main function to fetch and update schedule periodically."""
    init_outage_schedule_table()
    while True:
        schedule_entries, registry_update_time = fetch_schedule()
        if registry_update_time == datetime.min.replace(tzinfo=UTC_PLUS_2):
            # Fetching schedule failed
            time.sleep(CHECK_INTERVAL)
            continue
        if check_schedule_updated(registry_update_time):
            logger.info("Schedule update detected. Updating the database.")
            logger.info(f"Schedule last updated at: "
                        f"{registry_update_time.strftime('%Y-%m-%d %H:%M:%S')}")

            schedule_data = [(entry['start'], registry_update_time)
                             for entry in schedule_entries]
            update_outage_schedule(schedule_data)

            message = build_message(schedule_entries, registry_update_time)
            if message:
                logger.debug(f"Sending message:\n{message}")
                send_telegram_message(message, parse_mode='MarkdownV2')
            logger.info("Schedule updated and message sent.")
        else:
            logger.info("No new schedule data available.")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
