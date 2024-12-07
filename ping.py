# ping.py
import socket
import time
from datetime import datetime, timedelta

from loguru import logger

from config import CHECK_INTERVAL, HOST_TO_MONITOR, PORT_TO_MONITOR, UTC_PLUS_2
from db import (
    host_status_get_last_status,
    host_status_get_total_time,
    host_status_init,
    host_status_save_status,
)
from tg import format_duration, send_telegram_message


def is_server_available(host: str, port: int, timeout: int = 5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception as e:
        logger.error(f"Error checking host {host}: {e}")
        return False


def create_status_message(is_up: bool, duration: timedelta) -> str:
    """Create a status change message."""
    current_time = datetime.now(UTC_PLUS_2).strftime("%H:%M")
    duration_str = format_duration(duration)
    if is_up:
        return f"🟢 {current_time} Світло з'явилося\n🕓 Його не було {duration_str}"
    else:
        return f"🔴 {current_time} Світло зникло\n🕓 Воно було {duration_str}"


def main():
    """Main monitoring loop."""
    host_status_init()

    last_status = host_status_get_last_status()

    while True:
        current_status = is_server_available(HOST_TO_MONITOR, PORT_TO_MONITOR)

        if last_status is None:
            # Initial status
            host_status_save_status(current_status)
            status_str = "UP" if current_status else "DOWN"
            logger.info(f"Host {HOST_TO_MONITOR}:{PORT_TO_MONITOR}"
                        f"initial status is {status_str}")
            last_status = current_status
        elif current_status != last_status:
            # Status changed
            host_status_save_status(current_status)
            total_time = host_status_get_total_time(last_status)
            if total_time is None:
                total_time = timedelta()
            message = create_status_message(current_status, total_time)
            send_telegram_message(message)
            logger.info(message)
            last_status = current_status

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user.")
