import argparse
import logging
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from api_utils import APIUtils
from db_utils import DBUtils

logging.basicConfig(level=logging.INFO)
UTC_PLUS_2 = timezone(timedelta(hours=2))


class Monitor:
    def __init__(self, token: str, host: str, chat_ids: List[int], check_interval: int, db_file: str):
        self.token = token
        self.host = host
        self.chat_ids = chat_ids
        self.check_interval = check_interval
        self.group_id = "1"
        self.last_host_status: Optional[bool] = None
        self.status_change_time: Optional[datetime] = None
        self.db = DBUtils(db_file)
        self.api = APIUtils(self.group_id)

    def ping_host(self) -> bool:
        try:
            result = subprocess.run(
                ["ping", "-c", "20", "-W", "1", self.host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0
        except Exception as e:
            logging.error(f"Error pinging host: {e}")
            return False

    def send_message(self, message: str):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for chat_id in self.chat_ids:
            data = {"chat_id": chat_id, "text": message}
            try:
                response = requests.post(url, data=data)
                if response.status_code != 200:
                    logging.error(f"Failed to send message to {chat_id}: "
                                  f"{response.text}")
            except Exception as e:
                logging.error(f"Exception when sending message to {chat_id}: "
                              f"{e}")

    def monitor(self):
        self.db.init_db()
        self.last_host_status, self.status_change_time = self.db.get_last_status()

        while True:
            is_up = self.ping_host()
            if self.last_host_status is None:
                self.initialize_status(is_up)
            elif is_up != self.last_host_status:
                self.handle_status_change(is_up)
            time.sleep(self.check_interval)

    def initialize_status(self, is_up: bool):
        self.last_host_status = is_up
        self.db.save_status(is_up)
        self.status_change_time = datetime.now(timezone.utc)
        status_str = 'UP' if is_up else 'DOWN'
        message = f"Host {self.host} initial status is {status_str}"
        logging.info(message)

    def handle_status_change(self, is_up: bool):
        self.db.save_status(is_up)
        total_time = self.db.get_total_time(is_up)
        self.last_host_status = is_up
        self.status_change_time = datetime.now(timezone.utc)
        message = self.construct_message(is_up, total_time)
        self.send_message(message)
        logging.info(message)

    def construct_message(self, is_up: bool, total_time: Optional[timedelta]) -> str:
        current_time = datetime.now(UTC_PLUS_2).strftime('%H:%M')
        duration_str = self.format_duration(total_time)
        event_type, event_info = self.api.get_next_event(is_up)

        if is_up:
            message = (
                f"🟢 {current_time} Світло з'явилося\n"
                f"🕓 Його не було {duration_str}"
            )
            if event_info and event_type == 'outage':
                next_outage = self.format_event_time(event_info)
                message += f"\n🗓 Наступне планове: {next_outage}"
        else:
            message = (
                f"🔴 {current_time} Світло зникло\n"
                f"🕓 Воно було {duration_str}"
            )
            if event_info and event_type == 'available':
                expected_return = event_info['start'].strftime('%H:%M')
                message += f"\n🗓 Очікуємо за графіком о {expected_return}"

        return message

    @staticmethod
    def format_duration(total_time: Optional[timedelta]) -> str:
        if total_time:
            total_minutes = int(total_time.total_seconds() // 60)
            hours, minutes = divmod(total_minutes, 60)
            duration_parts = []
            if hours > 0:
                duration_parts.append(f"{hours}год")
            if minutes > 0 or hours == 0:
                duration_parts.append(f"{minutes}хв")
            return ' '.join(duration_parts)
        return "невідомо"

    @staticmethod
    def format_event_time(event_info: dict) -> str:
        start = event_info['start'].strftime('%H:%M')
        end = event_info['end'].strftime('%H:%M')
        return f"{start} - {end}"


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", type=str, required=True,
                        help="Telegram Bot API token")
    parser.add_argument("--host", type=str, default="8.8.8.8",
                        help="Host IP or domain to monitor")
    parser.add_argument("--chat-ids", nargs="+", type=int,
                        required=True, help="List of chat IDs to send messages to")
    parser.add_argument("--interval", type=int, default=60,
                        help="Host check interval in seconds (default: 60)")
    parser.add_argument("--db-file", type=str, default="state.db",
                        help="SQLite database file (default: state.db)")
    return parser


def main():
    args = create_parser().parse_args()
    monitor = Monitor(
        token=args.token,
        host=args.host,
        chat_ids=args.chat_ids,
        check_interval=args.interval,
        db_file=args.db_file,
    )
    monitor.monitor()


if __name__ == "__main__":
    main()
