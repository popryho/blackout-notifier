# schedule.py

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import requests
from loguru import logger

from config import CHECK_INTERVAL, DSO_ID, GROUP_ID, REGION_ID, UTC_PLUS_2
from db import (
    outage_schedule_init,
    outage_schedule_outdated,
    outage_schedule_update,
    schedule_update_tracker_init,
    schedule_update_tracker_outdated,
    schedule_update_tracker_update,
)
from tg import escape_markdown_v2, format_duration, send_telegram_message


def fetch_schedule() -> Dict | None:
    """Fetch the schedule from the API and return processed data."""
    uri = f"https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/{REGION_ID}/dsos/{DSO_ID}/planned-outages"
    current_time = datetime.now(UTC_PLUS_2)

    if current_time.time() < (datetime.min + timedelta(minutes=5)).time():
        logger.info("Skipping schedule fetching due to the time of the day.")
        time.sleep(300)
    try:
        response = requests.get(uri)
        data = response.json()
        return data[GROUP_ID]
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return


def process_schedule_data(data: Dict) -> List[Tuple[bool, datetime]]:
    schedules: List[Tuple[bool, datetime]] = []

    for day_label in ["today", "tomorrow"]:
        date = datetime.fromisoformat(data[day_label]["date"])
        schedules.extend(process_schedule(data[day_label]["slots"], date))
    return schedules


def process_schedule(
    schedule_data: List[Dict], date: datetime.date
) -> List[Tuple[bool, datetime]]:
    """Process schedule data and include only future updates.
    Returns a list of tuples (status, time) where status is True if NotPlanned, False if Definite."""
    schedule: List[Tuple[bool, datetime]] = []
    for slot in schedule_data:
        slot_type = slot.get("type")

        if slot_type not in ["NotPlanned", "Definite"]:
            logger.warning(f"Unknown slot type: {slot_type}")
            continue

        start_time = datetime(
            date.year,
            date.month,
            date.day,
            slot["start"] // 60,
            slot["start"] % 60,
            tzinfo=UTC_PLUS_2,
        )

        if start_time < datetime.now(UTC_PLUS_2):
            continue

        schedule.append((slot_type == "NotPlanned", start_time))
    return schedule


def build_message(
    schedule_entries: List[Tuple[bool, datetime]],
    updated_on: str,
) -> str:
    """Construct a Telegram message based on schedule entries."""
    header = (
        f"🗓️ Графік відключень, {escape_markdown_v2(GROUP_ID)} група\n"
        f"🔄 Оновлено: {escape_markdown_v2(datetime.fromisoformat(updated_on).strftime('%d.%m.%Y %H:%M'))}"
    )
    message_lines = [header]

    if not schedule_entries:
        message_lines.append("▪️ Наразі незаплановано")
        return "\n".join(message_lines)

    # Group entries by date and find NotPlanned periods
    grouped_by_date = {}
    for status, entry_time in schedule_entries:
        date_key = entry_time.date()
        if date_key not in grouped_by_date:
            grouped_by_date[date_key] = []
        grouped_by_date[date_key].append((status, entry_time))

    # Sort entries by time within each date
    for date_key in grouped_by_date:
        grouped_by_date[date_key].sort(key=lambda x: x[1])

    # Build message for each date
    for date, entries in sorted(grouped_by_date.items()):
        date_str = date.strftime("на *%d\\.%m\\.%Y*")
        message_lines.append(f"\n{date_str}")

        # Find Definite periods (where status is False)
        for i, (status, entry_time) in enumerate(entries):
            if not status:  # Definite starts
                # Find when it ends (next True entry)
                end_time = None
                for j in range(i + 1, len(entries)):
                    if entries[j][0]:  # Definite ends
                        end_time = entries[j][1]
                        break

                if end_time:
                    start_str = entry_time.strftime("%H:%M")
                    end_str = end_time.strftime("%H:%M")
                    duration_str = format_duration(end_time - entry_time)
                    line = f"▪️ {start_str} - {end_str}  [{duration_str}]"
                    message_lines.append(escape_markdown_v2(line))

    return "\n".join(message_lines)


def update_and_notify():
    """Fetch schedule, update database, and send notifications."""
    schedule_data: Dict = fetch_schedule()

    if not schedule_data:
        logger.error("Cannot fetch schedule data.")
        return

    updated_on = schedule_data["updatedOn"]
    if schedule_update_tracker_outdated(updated_on):
        schedule_update_tracker_update(updated_on)
    else:
        logger.info("Schedule update not detected.")
        return

    schedule_entries = process_schedule_data(schedule_data)

    if not schedule_entries:
        logger.error("Cannot process schedule data.")
        return

    if outage_schedule_outdated(schedule_entries):
        logger.info("Schedule update detected. Updating the database.")
        outage_schedule_update(schedule_entries)

        message = build_message(schedule_entries, updated_on)
        if message:
            send_telegram_message(message, parse_mode="MarkdownV2")
        logger.info("Schedule updated and message sent.")
    else:
        logger.info("No new schedule data available.")


def main():
    """Main function to initialize and periodically fetch schedule."""
    outage_schedule_init()
    schedule_update_tracker_init()

    while True:
        update_and_notify()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
