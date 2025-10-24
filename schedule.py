# schedule.py

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from loguru import logger

from config import CHECK_INTERVAL, DSO_ID, GROUP_ID, REGION_ID
from db import (
    OutageScheduleRepository,
    ScheduleUpdateTrackerRepository,
    get_database_manager,
)
from tg import escape_markdown_v2, format_duration, send_telegram_message

logger.remove()
logger.add(sys.stderr, level="INFO")

KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")


class SlotType(Enum):
    """Enum for schedule slot types."""

    NOT_PLANNED = "NotPlanned"
    DEFINITE = "Definite"


class ScheduleError(Exception):
    """Base exception for schedule-related errors."""

    pass


class ScheduleFetchError(ScheduleError):
    """Exception raised when schedule fetching fails."""

    pass


class ScheduleProcessingError(ScheduleError):
    """Exception raised when schedule processing fails."""

    pass


@dataclass
class ScheduleEntry:
    """Represents a single schedule entry."""

    status: bool  # True if NotPlanned, False if Definite
    start_time: datetime

    def __post_init__(self):
        """Validate the schedule entry after initialization."""
        if not isinstance(self.start_time, datetime):
            raise ValueError("start_time must be a datetime object")


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


# Constants
EARLY_MORNING_THRESHOLD = timedelta(minutes=5)
SLEEP_DURATION_EARLY_MORNING = 300  # 5 minutes


class ScheduleFetcher:
    """Handles fetching schedule data from the API."""

    def __init__(self, region_id: int, dso_id: int, group_id: str):
        self.region_id = region_id
        self.dso_id = dso_id
        self.group_id = group_id
        self.base_url = "https://app.yasno.ua/api/blackout-service/public/shutdowns"

    def _should_skip_early_morning(self) -> bool:
        """Check if we should skip fetching during early morning hours."""
        current_time = datetime.now(KYIV_TIMEZONE)
        return current_time.time() < (datetime.min + EARLY_MORNING_THRESHOLD).time()

    def _build_api_url(self) -> str:
        """Build the API URL for fetching schedule data."""
        return f"{self.base_url}/regions/{self.region_id}/dsos/{self.dso_id}/planned-outages"

    def fetch_schedule(self) -> Optional[ScheduleData]:
        """Fetch the schedule from the API and return processed data."""
        if self._should_skip_early_morning():
            logger.info("Skipping schedule fetching due to early morning hours.")
            time.sleep(SLEEP_DURATION_EARLY_MORNING)
            return None

        try:
            url = self._build_api_url()
            logger.debug(f"Fetching schedule from: {url}")

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            if self.group_id not in data:
                raise ScheduleFetchError(
                    f"Group ID '{self.group_id}' not found in API response"
                )

            return ScheduleData.from_api_response(data[self.group_id])

        except requests.RequestException as e:
            logger.error(f"Network error fetching schedule: {e}")
            raise ScheduleFetchError(f"Failed to fetch schedule: {e}")
        except KeyError as e:
            logger.error(f"Missing expected data in API response: {e}")
            raise ScheduleFetchError(f"Invalid API response format: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching schedule: {e}")
            raise ScheduleFetchError(f"Unexpected error: {e}")


class ScheduleProcessor:
    """Handles processing of schedule data."""

    def __init__(self):
        self.current_time = datetime.now(KYIV_TIMEZONE)

    def process_schedule_data(self, schedule_data: ScheduleData) -> List[ScheduleEntry]:
        """Process schedule data and return list of schedule entries."""
        entries = []

        for day_label in ["today", "tomorrow"]:
            day_data = getattr(schedule_data, day_label)
            date = datetime.fromisoformat(day_data["date"]).date()

            day_entries = self._process_day_schedule(day_data["slots"], date)

            logger.debug(f"Date: {date}")
            logger.debug(f"Day data slots: {day_data['slots']}")
            logger.debug(f"Day entries: {day_entries}")
            logger.debug(f"Status: {day_data['status']}")

            entries.extend(day_entries)

        return entries

    def _process_day_schedule(
        self, schedule_data: List[Dict], date: datetime.date
    ) -> List[ScheduleEntry]:
        """Process schedule data for a single day."""
        entries = []

        for slot in schedule_data:
            try:
                entry = self._process_slot(slot, date)
                if entry:
                    entries.append(entry)
            except Exception as e:
                logger.warning(f"Failed to process slot: {e}")
                continue

        return entries

    def _process_slot(self, slot: Dict, date: datetime.date) -> Optional[ScheduleEntry]:
        """Process a single schedule slot."""
        slot_type = slot.get("type")

        if slot_type not in [SlotType.NOT_PLANNED.value, SlotType.DEFINITE.value]:
            logger.warning(f"Unknown slot type: {slot_type}")
            return None

        start_time = self._parse_slot_time(slot["start"], date)

        # Skip past entries
        if start_time < self.current_time:
            return None

        status = slot_type == SlotType.NOT_PLANNED.value
        return ScheduleEntry(status=status, start_time=start_time)

    def _parse_slot_time(
        self, minutes_since_midnight: int, date: datetime.date
    ) -> datetime:
        """Parse slot time from minutes since midnight."""
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


class MessageBuilder:
    """Handles building Telegram messages from schedule data."""

    def __init__(self, group_id: str):
        self.group_id = group_id

    def build_message(
        self, schedule_entries: List[ScheduleEntry], updated_on: str
    ) -> str:
        """Construct a Telegram message based on schedule entries."""
        header = self._build_header(updated_on)
        message_lines = [header]

        if not schedule_entries:
            message_lines.append("▪️ Наразі незаплановано")
            return "\n".join(message_lines)

        grouped_entries = self._group_entries_by_date(schedule_entries)
        self._add_schedule_sections(message_lines, grouped_entries)

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

    def _group_entries_by_date(
        self, schedule_entries: List[ScheduleEntry]
    ) -> Dict[datetime.date, List[ScheduleEntry]]:
        """Group schedule entries by date."""
        grouped = {}
        for entry in schedule_entries:
            date_key = entry.start_time.date()
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append(entry)

        # Sort entries by time within each date
        for date_key in grouped:
            grouped[date_key].sort(key=lambda x: x.start_time)

        return grouped

    def _add_schedule_sections(
        self,
        message_lines: List[str],
        grouped_entries: Dict[datetime.date, List[ScheduleEntry]],
    ) -> None:
        """Add schedule sections to the message."""
        for date, entries in sorted(grouped_entries.items()):
            date_str = date.strftime("на *%d\\.%m\\.%Y*")
            message_lines.append(f"\n{date_str}")

            # Find definite periods (outages)
            self._add_outage_periods(message_lines, entries)

    def _add_outage_periods(
        self, message_lines: List[str], entries: List[ScheduleEntry]
    ) -> None:
        """Add outage periods to the message."""
        for i, entry in enumerate(entries):
            if not entry.status:  # Definite outage starts
                end_time = self._find_outage_end_time(entries, i)

                if end_time:
                    start_str = entry.start_time.strftime("%H:%M")
                    end_str = end_time.strftime("%H:%M")
                    duration_str = format_duration(end_time - entry.start_time)
                    line = f"▪️ {start_str} - {end_str}  [{duration_str}]"
                    message_lines.append(escape_markdown_v2(line))

    def _find_outage_end_time(
        self, entries: List[ScheduleEntry], start_index: int
    ) -> Optional[datetime]:
        """Find when an outage period ends."""
        for j in range(start_index + 1, len(entries)):
            if entries[j].status:  # Outage ends
                return entries[j].start_time
        return None


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
            raise ScheduleError(f"Database initialization failed: {e}")

    def update_and_notify(self) -> None:
        """Fetch schedule, update database, and send notifications."""
        try:
            # Fetch schedule data
            schedule_data = self.fetcher.fetch_schedule()
            logger.debug(f"Schedule data: {schedule_data}")
            if not schedule_data:
                logger.warning("No schedule data available.")
                return

            # Check if schedule was updated
            logger.debug(f"Updated on: {schedule_data.updated_on}")
            if not self._is_schedule_updated(schedule_data.updated_on):
                logger.debug("Schedule update not detected.")
                return

            # Process schedule entries
            schedule_entries = self.processor.process_schedule_data(schedule_data)
            logger.debug(f"Schedule entries: {schedule_entries}")
            if not schedule_entries:
                logger.warning("No valid schedule entries found.")
                return

            # Update database if needed
            if self._should_update_database(schedule_entries):
                self._update_database(schedule_entries, schedule_data.updated_on)
                self._send_notification(schedule_entries, schedule_data.updated_on)
                logger.info("Schedule updated and notification sent.")
            else:
                logger.info("No new schedule data available.")

        except ScheduleError as e:
            logger.error(f"Schedule error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in update_and_notify: {e}")

    def _is_schedule_updated(self, updated_on: str) -> bool:
        """Check if the schedule was updated."""
        if self.tracker_repo.is_outdated(updated_on):
            self.tracker_repo.update_tracker(updated_on)
            return True
        return False

    def _should_update_database(self, schedule_entries: List[ScheduleEntry]) -> bool:
        """Check if the database should be updated."""
        # Convert ScheduleEntry objects to tuples for compatibility with existing DB functions
        entries_as_tuples = [
            (entry.status, entry.start_time) for entry in schedule_entries
        ]
        return self.outage_repo.is_outdated(entries_as_tuples)

    def _update_database(
        self, schedule_entries: List[ScheduleEntry], updated_on: str
    ) -> None:
        """Update the database with new schedule data."""
        entries_as_tuples = [
            (entry.status, entry.start_time) for entry in schedule_entries
        ]
        self.outage_repo.update_schedule(entries_as_tuples)
        logger.info("Database updated with new schedule data.")

    def _send_notification(
        self, schedule_entries: List[ScheduleEntry], updated_on: str
    ) -> None:
        """Send notification message."""
        try:
            message = self.message_builder.build_message(schedule_entries, updated_on)
            if message:
                send_telegram_message(message, parse_mode="MarkdownV2")
                logger.info("Message sent successfully.")
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
