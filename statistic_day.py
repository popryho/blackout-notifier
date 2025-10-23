# statistic_day.py

from datetime import datetime, timedelta
from typing import Tuple

from loguru import logger

from config import UTC_PLUS_2
from db import (
    HostStatusRepository,
    get_database_manager,
)
from tg import format_duration, send_telegram_message


def get_time_range_for_yesterday() -> Tuple[datetime, datetime]:
    """Get the start and end timestamps for yesterday in UTC+2 timezone."""
    now = datetime.now(UTC_PLUS_2)
    yesterday = now - timedelta(days=1)
    start_of_day = datetime(
        yesterday.year, yesterday.month, yesterday.day, tzinfo=UTC_PLUS_2
    )
    end_of_day = start_of_day + timedelta(days=1) - timedelta(seconds=1)
    logger.debug(f"Time range for yesterday: {start_of_day} to {end_of_day}")
    return start_of_day, end_of_day


def build_message(
    date_str: str, total_on_time: timedelta, total_off_time: timedelta
) -> str:
    """Build the statistics message to be sent to Telegram."""
    message_header = f"💡 Статистика за вчора ({date_str}):\n"

    if total_off_time == timedelta():
        message_body = "\n🥳 Електрика була увесь день!"
    elif total_on_time == timedelta():
        message_body = "\n😞 Електрика була відсутня весь день."
    else:
        total_on_str = format_duration(total_on_time)
        total_off_str = format_duration(total_off_time)
        message_body = (
            f"\n🟢 Електрика присутня: {total_on_str}.\n"
            f"🔴 Електрика відсутня: {total_off_str}."
        )

    full_message = message_header + message_body
    logger.debug(f"Built message: {full_message}")
    return full_message


def calculate_total_times(
    start_time: datetime, end_time: datetime
) -> Tuple[timedelta, timedelta]:
    """Calculate total on and off times within the specified time range."""
    db_manager = get_database_manager()
    host_status_repo = HostStatusRepository(db_manager)

    rows = host_status_repo.get_changes_between(start_time, end_time)
    previous_status = host_status_repo.get_last_status_before(start_time)
    total_on_time = timedelta()
    total_off_time = timedelta()
    previous_time = start_time

    logger.debug(f"Previous status before start time: {previous_status}")
    logger.debug(f"Status changes: {rows}")

    if not rows:
        duration = end_time - start_time
        if previous_status:
            total_on_time = duration
            logger.debug(f"No status changes. Total on time: {duration}")
        else:
            total_off_time = duration
            logger.debug(f"No status changes. Total off time: {duration}")
        return total_on_time, total_off_time

    for time_dt, status in rows:
        duration = time_dt - previous_time
        if previous_status:
            total_on_time += duration
            logger.debug(f"Adding {duration} to total on time.")
        else:
            total_off_time += duration
            logger.debug(f"Adding {duration} to total off time.")
        previous_time = time_dt
        previous_status = status

    # Add the remaining time until the end of the day
    remaining_duration = end_time - previous_time
    if previous_status:
        total_on_time += remaining_duration
        logger.debug(f"Adding remaining {remaining_duration} to total on time.")
    else:
        total_off_time += remaining_duration
        logger.debug(f"Adding remaining {remaining_duration} to total off time.")

    return total_on_time, total_off_time


def send_daily_statistics() -> None:
    """Calculate statistics for yesterday and send the report via Telegram."""
    start_of_day, end_of_day = get_time_range_for_yesterday()
    date_str = start_of_day.strftime("%Y-%m-%d")

    total_on_time, total_off_time = calculate_total_times(start_of_day, end_of_day)

    message = build_message(date_str, total_on_time, total_off_time)
    send_telegram_message(message)


def main() -> None:
    """Main function to execute the daily statistics reporting."""
    db_manager = get_database_manager()
    host_status_repo = HostStatusRepository(db_manager)
    host_status_repo.initialize_table()

    logger.info("Starting daily statistics reporting.")
    send_daily_statistics()
    logger.info("Daily statistics reporting completed.")


if __name__ == "__main__":
    main()
