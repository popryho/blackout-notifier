# schedule.py

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import requests
from loguru import logger

from config import CHECK_INTERVAL, GROUP_ID, UTC_PLUS_2
from db import check_schedule_updated, init_schedule_table, update_schedule
from tg import send_telegram_message, format_duration


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
    today_date = current_time.date()
    tomorrow_date = today_date + timedelta(days=1)
    schedules = []

    for day_label, date in [('today', today_date), ('tomorrow', tomorrow_date)]:
        try:
            schedule_data = data['components'][4]['dailySchedule']['kiev'][day_label]['groups'][group_number]
            day_schedule = process_schedule(schedule_data, date)
            schedules.extend(day_schedule)
        except (KeyError, IndexError):
            logger.warning(
                f"{day_label.capitalize()}'s schedule is not available.")

    registry_update_timestamp = data['components'][4]['lastRegistryUpdateTime']
    registry_update_time = datetime.fromtimestamp(
        registry_update_timestamp, UTC_PLUS_2)

    return schedules, registry_update_time


def process_schedule(schedule_data, date) -> List[Dict]:
    """Process schedule data for a specific date."""
    schedule = []
    for interval in schedule_data:
        start_hour = interval['start']
        status = False if interval['type'] == 'DEFINITE_OUTAGE' else True
        time_slot = datetime(date.year, date.month, date.day,
                             start_hour, tzinfo=UTC_PLUS_2)
        if time_slot < datetime.now(UTC_PLUS_2):
            continue
        schedule.append({
            'start': time_slot,
            'end': time_slot + timedelta(hours=1),
            'status': status})
    return schedule


def merge_intervals(intervals: List[Dict]) -> List[Dict]:
    """Merge consecutive intervals with the same status."""
    if not intervals:
        return []
    intervals.sort(key=lambda x: x['start'])
    merged = [intervals[0]]
    for current in intervals[1:]:
        last = merged[-1]
        if current['start'] == last['end'] and current['status'] == last['status']:
            last['end'] = current['end']
        else:
            merged.append(current)
    return merged


def build_message(merged_intervals: List[Dict], registry_update_time: datetime) -> str:
    """Build the Telegram message based on merged intervals."""
    if not merged_intervals:
        return ""

    # Format the update time
    update_time_str = f"🗓️ Графік відключень, {GROUP_ID} група\n🔄 Оновлено: {
        registry_update_time.strftime('%d-%m %H:%M')}"
    message_lines = [update_time_str]

    for interval in merged_intervals:
        if not interval['status']:  # Only include status = False
            start_str = interval['start'].strftime("%H:%M")
            end_str = interval['end'].strftime("%H:%M")
            duration = interval['end'] - interval['start']
            duration_str = format_duration(duration)
            line = f"▪️{start_str} - {end_str}  [{duration_str}]"
            message_lines.append(line)

    message = "\n".join(message_lines)
    return message


def main():
    """Main function to fetch and update schedule periodically."""
    init_schedule_table()
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

            schedule_data = [(entry['status'], entry['start'], registry_update_time)
                             for entry in schedule_entries]
            update_schedule(schedule_data)

            merged = merge_intervals(schedule_entries)
            merged_outages = [interval for interval in merged
                              if not interval['status']]
            message = build_message(merged_outages, registry_update_time)
            if message:
                send_telegram_message(message, parse_mode='HTML')
            logger.info("Schedule updated and message sent.")
        else:
            logger.info("No new schedule data available.")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
