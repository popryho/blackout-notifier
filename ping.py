# ping.py

import subprocess
import time
from datetime import datetime, timedelta

from loguru import logger

from config import CHECK_INTERVAL, HOST_TO_MONITOR, UTC_PLUS_2
from db import get_last_status, get_total_time, init_host_status_table, save_status
from tg import format_duration, send_telegram_message


def ping_host(host: str) -> bool:
    """Ping the specified host to check its status."""
    try:
        result = subprocess.run(
            ["ping", "-c", "20", "-W", "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error pinging host {host}: {e}")
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
    init_host_status_table()

    last_status = get_last_status()

    while True:
        current_status = ping_host(HOST_TO_MONITOR)

        if last_status is None:
            # Initial status
            save_status(current_status)
            status_str = "UP" if current_status else "DOWN"
            logger.info(f"Host {HOST_TO_MONITOR} initial status is {status_str}")
        elif current_status != last_status:
            # Status changed
            save_status(current_status)
            total_time = get_total_time(last_status)
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
