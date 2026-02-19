import subprocess
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Optional

from loguru import logger

from config import CHECK_INTERVAL, HOST_TO_MONITOR, AVAILABILITY_WINDOW
from db import (
    HostStatusRepository,
    get_database_manager,
)
from tg import format_duration, send_telegram_message


class ConnectionStatus(Enum):
    UP = True
    DOWN = False


@dataclass
class HostConfig:
    """Configuration for host monitoring."""
    host: str
    timeout: int = 5
    check_interval: int = 60
    availability_window: int = 30  # How long to try before giving up (seconds)
    retry_gap: int = 2             # Sleep between retries within the window (seconds)

    def __post_init__(self):
        if not self.host:
            raise ValueError("Host cannot be empty")
        if self.availability_window < self.timeout:
            logger.warning("Availability window is shorter than ping timeout.")


@dataclass
class StatusChange:
    new_status: bool
    duration: timedelta

    @property
    def is_up(self) -> bool:
        return self.new_status == ConnectionStatus.UP.value


class ConnectionChecker:
    """Handles ICMP ping checks to a host with retry logic."""

    def __init__(self, config: HostConfig):
        self.config = config

    def _single_ping_attempt(self) -> bool:
        """
        Send a single ICMP ping.
        Returns True if the host responds, False otherwise.
        """
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(self.config.timeout), self.config.host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Unexpected error during ping: {e}")
            return False

    def is_server_available(self) -> bool:
        """
        Determines availability based on a time window.

        Logic:
        1. Attempt to ping.
        2. If successful: Return True immediately (short-circuit).
        3. If failed: Wait `retry_gap` and try again.
        4. If time exceeds `availability_window`: Return False.

        This filters out transient packet loss. If ANY ping gets through
        in the window, the host is considered UP.
        """
        start_time = time.time()
        end_time = start_time + self.config.availability_window
        attempt_count = 0

        while time.time() < end_time:
            attempt_count += 1
            if self._single_ping_attempt():
                if attempt_count > 1:
                    logger.info(f"Ping recovered after {attempt_count} attempts.")
                return True

            # Wait before next retry, but don't oversleep past end_time
            time.sleep(self.config.retry_gap)

        logger.warning(
            f"Host unreachable. Failed all ping attempts over {self.config.availability_window}s window."
        )
        return False


class MessageBuilder:
    @staticmethod
    def create_status_message(status_change: StatusChange) -> str:
        duration_str = format_duration(status_change.duration)
        if status_change.is_up:
            return f"🟢 Світло з'явилося\n🕓 Його не було {duration_str}"
        else:
            return f"🔴 Світло зникло\n🕓 Воно було {duration_str}"


class HostMonitor:
    def __init__(self, config: HostConfig):
        self.config = config
        self.checker = ConnectionChecker(config)
        self.message_builder = MessageBuilder()
        self.last_status: Optional[bool] = None

        self.db_manager = get_database_manager()
        self.host_status_repo = HostStatusRepository(self.db_manager)

    def initialize(self) -> None:
        try:
            self.host_status_repo.initialize_table()
            self.last_status = self.host_status_repo.get_last_status()
            logger.info(f"Monitor initialized. Previous known status: {self.last_status}")
        except Exception as e:
            logger.error(f"Failed to initialize monitoring: {e}")
            raise

    def process_status_change(self, current_status: bool) -> None:
        """
        Compare current status with last known status and act if changed.
        """
        # Case 1: First run (DB was empty)
        if self.last_status is None:
            self.host_status_repo.save_status(current_status)
            self.last_status = current_status
            logger.info(f"Initial status recorded: {'UP' if current_status else 'DOWN'}")
            return

        # Case 2: Status is the same -> Do nothing
        if current_status == self.last_status:
            return

        # Case 3: Status changed
        logger.info(f"Status changed: {self.last_status} -> {current_status}")

        # 1. Save new status immediately
        self.host_status_repo.save_status(current_status)

        # 2. Calculate how long we were in the PREVIOUS state
        duration = self.host_status_repo.get_total_time(self.last_status) or timedelta()

        # 3. Notify
        change_event = StatusChange(new_status=current_status, duration=duration)
        msg = self.message_builder.create_status_message(change_event)

        self._send_notification(msg)
        logger.info(msg)

        # 4. Update memory state
        self.last_status = current_status

    def _send_notification(self, message: str) -> None:
        try:
            send_telegram_message(message)
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def run(self) -> None:
        logger.info(f"Starting ping monitoring loop for {self.config.host}")

        while True:
            try:
                # 1. Perform the check (may take up to availability_window seconds)
                is_up = self.checker.is_server_available()

                # 2. Process results
                self.process_status_change(is_up)

            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user.")
                break
            except Exception as e:
                logger.error(f"Critical error in monitoring loop: {e}")
                time.sleep(5)

            # 3. Wait for the next check cycle
            time.sleep(self.config.check_interval)


def main():
    config = HostConfig(
        host=HOST_TO_MONITOR,
        timeout=5,
        check_interval=CHECK_INTERVAL,
        availability_window=AVAILABILITY_WINDOW,
        retry_gap=2,
    )

    monitor = HostMonitor(config)
    monitor.initialize()
    monitor.run()


if __name__ == "__main__":
    main()
