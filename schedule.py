# schedule.py

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests
from loguru import logger

from config import CHECK_INTERVAL, DSO_ID, GROUP_ID, KYIV_TIMEZONE, REGION_ID
from db import (
    OutageScheduleRepository,
    ScheduleUpdateTrackerRepository,
    get_database_manager,
)
from tg import escape_markdown_v2, format_duration, send_telegram_message

logger.remove()
logger.add(sys.stderr, level="INFO")


def parse_slot_time(minutes_since_midnight: int, date: datetime.date) -> datetime:
    """Parse slot time from minutes since midnight."""

    if minutes_since_midnight == 1440:  # 24:00
        return datetime(
            date.year,
            date.month,
            date.day,
            23,
            59,
            tzinfo=KYIV_TIMEZONE,
        )

    hours = minutes_since_midnight // 60
    minutes = minutes_since_midnight % 60

    return datetime(
        date.year,
        date.month,
        date.day,
        hours,
        minutes,
        tzinfo=KYIV_TIMEZONE,
    )


class SlotType(Enum):
    """Enum for schedule slot types."""

    NOT_PLANNED = "NotPlanned"
    DEFINITE = "Definite"


@dataclass
class ScheduleData:
    """Represents the complete schedule data from API."""

    today: Dict
    tomorrow: Dict
    updated_on: str

    @classmethod
    def from_api_response(cls, data: Dict) -> "ScheduleData":
        """Create ScheduleData from API response."""
        return cls(
            today=data["today"], tomorrow=data["tomorrow"], updated_on=data["updatedOn"]
        )


class ScheduleFetcher:
    """Handles fetching schedule data from the API."""

    def __init__(self, region_id: int, dso_id: int, group_id: str):
        self.region_id = region_id
        self.dso_id = dso_id
        self.group_id = group_id
        self.base_url = "https://app.yasno.ua/api/blackout-service/public/shutdowns"

    def _build_api_url(self) -> str:
        """Build the API URL for fetching schedule data."""
        return f"{self.base_url}/regions/{self.region_id}/dsos/{self.dso_id}/planned-outages"

    def fetch_schedule(self) -> Optional[ScheduleData]:
        """Fetch the schedule from the API and return processed data."""
        try:
            url = self._build_api_url()
            logger.debug(f"Fetching schedule from: {url}")

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            if self.group_id not in data:
                logger.error(f"Group ID '{self.group_id}' not found in API response")
                return None

            return ScheduleData.from_api_response(data[self.group_id])

        except requests.RequestException as e:
            logger.error(f"Network error fetching schedule: {e}")
            return None
        except KeyError as e:
            logger.error(f"Missing expected data in API response: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching schedule: {e}")
            return None


class ScheduleProcessor:
    """Handles processing of schedule data."""

    def process_schedule_to_database_entries(
        self, schedule_data: ScheduleData
    ) -> Dict[datetime.date, List[Tuple[bool, datetime]]]:
        """Convert API schedule data to database entries grouped by date."""
        entries_by_date = {}

        for day_label in ["today", "tomorrow"]:
            day_data = getattr(schedule_data, day_label)
            slots = day_data["slots"]

            # Skip if no slots for this day
            if not slots:
                logger.debug(f"No slots for {day_label}")
                continue

            date = datetime.fromisoformat(day_data["date"]).date()
            day_entries = []

            for slot in slots:
                slot_type = slot.get("type")
                if slot_type not in [
                    SlotType.NOT_PLANNED.value,
                    SlotType.DEFINITE.value,
                ]:
                    logger.warning(f"Unknown slot type: {slot_type}")
                    continue

                start_time = parse_slot_time(slot["start"], date)
                status = slot_type == SlotType.NOT_PLANNED.value
                day_entries.append((status, start_time))

            if day_entries:
                entries_by_date[date] = day_entries
                logger.debug(f"Date: {date}, entries: {len(day_entries)}")

        return entries_by_date


class MessageBuilder:
    """Handles building Telegram messages from schedule data."""

    def __init__(self, group_id: str):
        self.group_id = group_id

    def build_message(self, schedule_data: ScheduleData) -> str:
        """Construct a Telegram message from schedule data."""
        header = self._build_header(schedule_data.updated_on)
        message_lines = [header]

        has_outages = False
        current_time = datetime.now(KYIV_TIMEZONE)

        for day_label in ["today", "tomorrow"]:
            day_data = getattr(schedule_data, day_label)
            slots = day_data["slots"]

            if not slots:
                continue

            date = datetime.fromisoformat(day_data["date"]).date()

            # Filter only Definite slots
            definite_slots = [
                slot for slot in slots if slot.get("type") == SlotType.DEFINITE.value
            ]

            if not definite_slots:
                continue

            # Convert to outage periods and filter out past ones
            outage_periods = []
            for slot in definite_slots:
                start_time = parse_slot_time(slot["start"], date)
                end_time = parse_slot_time(slot["end"], date)

                # Skip past outages
                if end_time < current_time:
                    continue

                outage_periods.append((start_time, end_time))

            if outage_periods:
                has_outages = True
                date_str = date.strftime("на *%d\\.%m\\.%Y*")
                message_lines.append(f"\n{date_str}")

                for start_time, end_time in outage_periods:
                    start_str = start_time.strftime("%H:%M")
                    end_str = end_time.strftime("%H:%M")
                    duration_str = format_duration(end_time - start_time)
                    line = f"▪️ {start_str} - {end_str}  [{duration_str}]"
                    message_lines.append(escape_markdown_v2(line))

        if not has_outages:
            message_lines.append("▪️ Наразі незаплановано")

        return "\n".join(message_lines)

    def _build_header(self, updated_on: str) -> str:
        """Build the message header."""
        dt = datetime.fromisoformat(updated_on)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        kyiv_time = dt.astimezone(KYIV_TIMEZONE)
        formatted_time = kyiv_time.strftime("%d.%m.%Y %H:%M")
        return (
            f"🗓️ Графік відключень, {escape_markdown_v2(self.group_id)} група\n"
            f"🔄 Оновлено: {escape_markdown_v2(formatted_time)}"
        )


class ScheduleManager:
    """Main class that orchestrates schedule fetching, processing, and notifications."""

    def __init__(self, region_id: int, dso_id: int, group_id: str, check_interval: int):
        self.fetcher = ScheduleFetcher(region_id, dso_id, group_id)
        self.processor = ScheduleProcessor()
        self.message_builder = MessageBuilder(group_id)
        self.check_interval = check_interval

        # Initialize database repositories
        self.db_manager = get_database_manager()
        self.outage_repo = OutageScheduleRepository(self.db_manager)
        self.tracker_repo = ScheduleUpdateTrackerRepository(self.db_manager)

    def initialize_database(self) -> None:
        """Initialize database tables."""
        try:
            self.outage_repo.initialize_table()
            self.tracker_repo.initialize_table()
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def update_and_notify(self) -> None:
        """Fetch schedule, update database, and send notifications."""
        try:
            # Fetch schedule data
            schedule_data = self.fetcher.fetch_schedule()
            if not schedule_data:
                logger.warning("No schedule data available.")
                return

            # Check if schedule was updated
            if not self._is_schedule_updated(schedule_data.updated_on):
                logger.debug("Schedule not updated since last check.")
                return
            logger.info(f"Schedule updated: {schedule_data.updated_on}")
            logger.info(f"Schedule data: {schedule_data}")

            # Update database with new schedule (only if changed)
            has_changes = self._update_database(schedule_data)

            # Send notification only if there were actual changes
            if has_changes:
                self._send_notification(schedule_data)
                logger.info("Schedule updated and notification sent.")
            else:
                logger.info(
                    "Schedule metadata updated but no content changes detected."
                )

        except Exception as e:
            logger.error(f"Error in update_and_notify: {e}")

    def _is_schedule_updated(self, updated_on: str) -> bool:
        """Check if the schedule was updated."""
        if self.tracker_repo.has_schedule_changed(updated_on):
            self.tracker_repo.save_last_updated_time(updated_on)
            return True
        return False

    def _has_schedule_changed_for_date(
        self, date: datetime.date, new_entries: List[Tuple[bool, datetime]]
    ) -> bool:
        """Check if schedule has changed for a specific date.

        Args:
            date: The date to check
            new_entries: New schedule entries as list of (status, time) tuples

        Returns:
            True if schedule has changed, False otherwise.
        """
        start_time = datetime.combine(date, datetime.min.time(), tzinfo=KYIV_TIMEZONE)
        end_time = start_time + timedelta(days=1) - timedelta(seconds=1)

        # Get existing entries from database
        existing_entries = self.outage_repo.get_schedule_between(start_time, end_time)

        # Compare entries
        return not self._are_schedule_entries_equal(new_entries, existing_entries)

    def _are_schedule_entries_equal(
        self,
        new_entries: List[Tuple[bool, datetime]],
        existing_entries: List[Tuple[datetime, bool]],
    ) -> bool:
        """Compare two lists of schedule entries for equality.

        Args:
            new_entries: New entries as list of (status, time) tuples
            existing_entries: Existing entries from DB as list of (time, status) tuples

        Returns:
            True if entries are equal, False otherwise.
        """
        if len(new_entries) != len(existing_entries):
            return False

        # Convert new_entries to (time, status) format and sort by time
        new_sorted = sorted([(time, status) for status, time in new_entries])
        existing_sorted = sorted(existing_entries)

        # Compare each entry
        for (new_time, new_status), (existing_time, existing_status) in zip(
            new_sorted, existing_sorted
        ):
            # Compare times (allowing for small differences due to microseconds)
            if abs((new_time - existing_time).total_seconds()) > 1:
                return False
            if new_status != existing_status:
                return False

        return True

    def _update_database(self, schedule_data: ScheduleData) -> bool:
        """Update the database with new schedule data.

        Returns:
            True if any changes were made, False otherwise.
        """
        entries_by_date = self.processor.process_schedule_to_database_entries(
            schedule_data
        )

        if not entries_by_date:
            logger.info("No schedule entries to update in database.")
            return False

        # Process each day separately, only updating if changed
        dates_updated = []
        for date, new_entries in entries_by_date.items():
            if self._has_schedule_changed_for_date(date, new_entries):
                start_time = datetime.combine(
                    date, datetime.min.time(), tzinfo=KYIV_TIMEZONE
                )
                end_time = start_time + timedelta(days=1)
                self.outage_repo.clear_schedule_between(start_time, end_time)
                self.outage_repo.insert_schedule_entries(new_entries)
                dates_updated.append(date)
            else:
                logger.info(
                    f"No changes detected for {date}, skipping database update."
                )

        if dates_updated:
            total_entries = sum(len(entries_by_date[date]) for date in dates_updated)
            logger.info(
                f"Database updated for {len(dates_updated)} date(s) with {total_entries} total entries."
            )
            return True
        else:
            logger.info("No schedule changes detected for any date.")
            return False

    def _send_notification(self, schedule_data: ScheduleData) -> None:
        """Send notification message."""
        try:
            message = self.message_builder.build_message(schedule_data)
            if message:
                send_telegram_message(message, parse_mode="MarkdownV2")
                logger.info("Notification sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def run(self) -> None:
        """Main loop for continuous schedule monitoring."""
        logger.info("Starting schedule monitoring...")

        while True:
            try:
                self.update_and_notify()
            except KeyboardInterrupt:
                logger.info("Schedule monitoring stopped by user.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")

            time.sleep(self.check_interval)


def main():
    """Main function to initialize and run the schedule manager."""
    try:
        scheduler = ScheduleManager(
            region_id=REGION_ID,
            dso_id=DSO_ID,
            group_id=GROUP_ID,
            check_interval=CHECK_INTERVAL,
        )

        scheduler.initialize_database()
        scheduler.run()

    except Exception as e:
        logger.error(f"Failed to start schedule manager: {e}")
        raise


if __name__ == "__main__":
    main()
