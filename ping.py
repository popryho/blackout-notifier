# ping.py
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from loguru import logger

from config import CHECK_INTERVAL, HOST_TO_MONITOR, PORT_TO_MONITOR, UTC_PLUS_2
from db import (
    HostStatusRepository,
    get_database_manager,
)
from tg import format_duration, send_telegram_message


class ConnectionStatus(Enum):
    """Enum for connection status."""

    UP = True
    DOWN = False


class MonitoringError(Exception):
    """Base exception for monitoring-related errors."""

    pass


class ConnectionError(MonitoringError):
    """Exception raised when connection fails."""

    pass


@dataclass
class HostConfig:
    """Configuration for host monitoring."""

    host: str
    port: int
    timeout: int = 5
    check_interval: int = 60

    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.host:
            raise ValueError("Host cannot be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError("Port must be between 1 and 65535")
        if self.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if self.check_interval <= 0:
            raise ValueError("Check interval must be positive")


@dataclass
class StatusChange:
    """Represents a status change event."""

    new_status: bool
    duration: timedelta
    timestamp: datetime

    @property
    def is_up(self) -> bool:
        """Check if the new status is UP."""
        return self.new_status == ConnectionStatus.UP.value

    @property
    def is_down(self) -> bool:
        """Check if the new status is DOWN."""
        return self.new_status == ConnectionStatus.DOWN.value


class ConnectionChecker:
    """Handles connection checking to a host."""

    def __init__(self, config: HostConfig):
        self.config = config

    def is_server_available(self) -> bool:
        """Check if the server is available."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.config.timeout)
                s.connect((self.config.host, self.config.port))
                return True
        except socket.timeout:
            logger.warning(
                f"Connection timeout to {self.config.host}:{self.config.port}"
            )
            return False
        except socket.error as e:
            logger.warning(
                f"Connection error to {self.config.host}:{self.config.port}: {e}"
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error checking {self.config.host}:{self.config.port}: {e}"
            )
            return False


class MessageBuilder:
    """Handles building status change messages."""

    @staticmethod
    def create_status_message(status_change: StatusChange) -> str:
        """Create a status change message."""
        current_time = status_change.timestamp.strftime("%H:%M")
        duration_str = format_duration(status_change.duration)

        if status_change.is_up:
            return f"🟢 {current_time} Світло з'явилося\n🕓 Його не було {duration_str}"
        else:
            return f"🔴 {current_time} Світло зникло\n🕓 Воно було {duration_str}"


class HostMonitor:
    """Main class for monitoring host connectivity."""

    def __init__(self, config: HostConfig):
        self.config = config
        self.connection_checker = ConnectionChecker(config)
        self.message_builder = MessageBuilder()
        self.last_status: Optional[bool] = None

        # Initialize database repository
        self.db_manager = get_database_manager()
        self.host_status_repo = HostStatusRepository(self.db_manager)

    def initialize(self) -> None:
        """Initialize the monitoring system."""
        try:
            self.host_status_repo.initialize_table()
            logger.info(
                f"Host monitoring initialized for {self.config.host}:{self.config.port}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize monitoring: {e}")
            raise MonitoringError(f"Initialization failed: {e}")

    def get_last_status(self) -> Optional[bool]:
        """Get the last known status from database."""
        try:
            return self.host_status_repo.get_last_status()
        except Exception as e:
            logger.error(f"Failed to get last status: {e}")
            return None

    def save_status(self, status: bool) -> None:
        """Save current status to database."""
        try:
            self.host_status_repo.save_status(status)
        except Exception as e:
            logger.error(f"Failed to save status: {e}")
            raise MonitoringError(f"Failed to save status: {e}")

    def get_duration_since_last_change(self, previous_status: bool) -> timedelta:
        """Get duration since last status change."""
        try:
            total_time = self.host_status_repo.get_total_time(previous_status)
            return total_time if total_time is not None else timedelta()
        except Exception as e:
            logger.error(f"Failed to get duration: {e}")
            return timedelta()

    def handle_status_change(self, current_status: bool) -> None:
        """Handle a status change event."""
        if self.last_status is None:
            # Initial status
            self.save_status(current_status)
            status_str = "UP" if current_status else "DOWN"
            logger.info(
                f"Host {self.config.host}:{self.config.port} initial status is {status_str}"
            )
            self.last_status = current_status
            return

        if current_status != self.last_status:
            # Status changed
            self.save_status(current_status)
            duration = self.get_duration_since_last_change(self.last_status)

            status_change = StatusChange(
                new_status=current_status,
                duration=duration,
                timestamp=datetime.now(UTC_PLUS_2),
            )

            message = self.message_builder.create_status_message(status_change)
            self._send_notification(message)
            logger.info(message)
            self.last_status = current_status

    def _send_notification(self, message: str) -> None:
        """Send notification message."""
        try:
            send_telegram_message(message)
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def check_once(self) -> None:
        """Perform a single connectivity check."""
        try:
            current_status = self.connection_checker.is_server_available()
            self.handle_status_change(current_status)
        except Exception as e:
            logger.error(f"Error during connectivity check: {e}")

    def run(self) -> None:
        """Main monitoring loop."""
        logger.info("Starting host monitoring...")

        # Initialize last status from database
        self.last_status = self.get_last_status()

        while True:
            try:
                self.check_once()
            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in monitoring loop: {e}")

            time.sleep(self.config.check_interval)


def main():
    """Main function to run host monitoring."""
    try:
        config = HostConfig(
            host=HOST_TO_MONITOR,
            port=PORT_TO_MONITOR,
            timeout=5,
            check_interval=CHECK_INTERVAL,
        )

        monitor = HostMonitor(config)
        monitor.initialize()
        monitor.run()

    except Exception as e:
        logger.error(f"Failed to start monitoring: {e}")
        raise


if __name__ == "__main__":
    main()
