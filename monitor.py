import argparse
import logging
import subprocess
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests
from emoji import emojize

logging.basicConfig(level=logging.INFO)

UTC_PLUS_2 = timezone(timedelta(hours=2))


class Monitor:
    def __init__(
        self,
        token: str,
        host: str,
        chat_ids: List[int],
        check_interval: int,
        db_file: str,
    ):
        self.token = token
        self.host = host
        self.chat_ids = chat_ids
        self.check_interval = check_interval
        self.db_file = db_file
        self.group_id = "1"  # Hardcoded group ID
        self.last_host_status: Optional[bool] = None
        self.status_change_time: Optional[datetime] = None

    def init_db(self):
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS host_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status INTEGER NOT NULL,
                    time TEXT NOT NULL
                )
                """
            )

    def save_status(self, status: bool):
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_file) as conn:
            conn.execute(
                "INSERT INTO host_status (status, time) VALUES (?, ?)",
                (int(status), now),
            )

    def get_last_status(self) -> Tuple[Optional[bool], Optional[datetime]]:
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, time FROM host_status ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
        if row:
            status = bool(row[0])
            time = datetime.fromisoformat(row[1])
            return status, time
        return None, None

    def get_total_time(self, current_status: bool) -> Optional[timedelta]:
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT time FROM host_status WHERE status = ? ORDER BY id DESC LIMIT 1",
                (int(not current_status),),
            )
            row = cursor.fetchone()
        if row:
            last_change_time = datetime.fromisoformat(row[0])
            total_time = datetime.now(timezone.utc) - last_change_time
            return total_time
        return None

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
            data = {
                "chat_id": chat_id,
                "text": message,
            }
            try:
                response = requests.post(url, data=data)
                if response.status_code != 200:
                    logging.error(
                        f"Failed to send message to {chat_id}: {response.text}")
            except Exception as e:
                logging.error(
                    f"Exception when sending message to {chat_id}: {e}")

    def get_next_event(self, is_up: bool):
        current_time = datetime.now(UTC_PLUS_2)
        url = "https://api.yasno.com.ua/api/v1/pages/home/schedule-turn-off-electricity"
        try:
            response = requests.get(url)
            data = response.json()
        except Exception as e:
            logging.error(f"Error fetching schedule: {e}")
            return None, None

        group_number = self.group_id
        today_date = current_time.date()
        tomorrow_date = today_date + timedelta(days=1)
        today_schedule = []
        tomorrow_schedule = []

        try:
            today_schedule = data['components'][4]['dailySchedule']['kiev']['today']['groups'][group_number]
        except (KeyError, IndexError):
            logging.warning("Today's schedule is not available.")
        try:
            tomorrow_schedule = data['components'][4]['dailySchedule']['kiev']['tomorrow']['groups'][group_number]
        except (KeyError, IndexError):
            logging.warning("Tomorrow's schedule is not available.")

        if not today_schedule and not tomorrow_schedule:
            logging.warning("No schedule information available.")
            return None, None

        def convert_schedule_to_datetime(schedule, date):
            datetime_intervals = []
            for interval in schedule:
                start_hour = interval['start']
                end_hour = interval['end']
                start_datetime = datetime.combine(date, datetime.min.time()).replace(
                    hour=start_hour, tzinfo=UTC_PLUS_2)
                end_datetime = datetime.combine(date, datetime.min.time()).replace(
                    hour=end_hour % 24, tzinfo=UTC_PLUS_2)
                if end_hour >= 24 or end_hour < start_hour:
                    end_datetime += timedelta(days=1)
                datetime_intervals.append(
                    {'start': start_datetime, 'end': end_datetime,
                        'type': interval['type']}
                )
            return datetime_intervals

        full_intervals = []
        if today_schedule:
            today_intervals = convert_schedule_to_datetime(
                today_schedule, today_date)
            full_intervals.extend(today_intervals)
        if tomorrow_schedule:
            tomorrow_intervals = convert_schedule_to_datetime(
                tomorrow_schedule, tomorrow_date)
            full_intervals.extend(tomorrow_intervals)

        def merge_intervals(intervals):
            if not intervals:
                return []
            intervals.sort(key=lambda x: x['start'])
            merged = [intervals[0]]
            for current in intervals[1:]:
                last = merged[-1]
                if current['start'] == last['end'] and current['type'] == last['type']:
                    last['end'] = current['end']
                else:
                    merged.append(current)
            return merged

        merged_intervals = merge_intervals(full_intervals)

        def is_currently_in_outage(current_time, intervals):
            for interval in intervals:
                if interval['start'] <= current_time < interval['end']:
                    return True, interval
            return False, None

        electricity_state = 'on' if is_up else 'off'
        in_outage, current_interval = is_currently_in_outage(
            current_time, merged_intervals)

        def find_next_event(current_time, intervals, state):
            if state == 'on':
                for interval in intervals:
                    if interval['start'] > current_time:
                        return 'outage', interval
                return None, None
            elif state == 'off':
                if in_outage:
                    return 'available', {'start': current_interval['end']}
                else:
                    for interval in intervals:
                        if interval['start'] > current_time:
                            return 'available', {'start': interval['end']}
                    return None, None
            else:
                logging.error("Invalid state.")
                return None, None

        event_type, event_info = find_next_event(
            current_time, merged_intervals, electricity_state)
        return event_type, event_info

    def monitor(self):
        self.init_db()
        self.last_host_status, self.status_change_time = self.get_last_status()

        while True:
            try:
                is_up = self.ping_host()
            except Exception as e:
                logging.error(f"Error pinging host: {e}")
                is_up = False

            if self.last_host_status is None:
                self.last_host_status = is_up
                self.save_status(is_up)
                self.status_change_time = datetime.now(timezone.utc)
                status_str = 'UP' if is_up else 'DOWN'
                message = f"Host {self.host} initial status is {status_str}"
                logging.info(message)
            elif is_up != self.last_host_status:
                self.save_status(is_up)
                total_time = self.get_total_time(is_up)
                self.last_host_status = is_up
                self.status_change_time = datetime.now(timezone.utc)

                current_time = datetime.now(UTC_PLUS_2)
                current_time_str = current_time.strftime('%H:%M')
                if total_time:
                    hours, remainder = divmod(
                        int(total_time.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    duration_str = f"{hours}год {minutes}хв"
                else:
                    duration_str = "невідомо"

                event_type, event_info = self.get_next_event(is_up)

                if is_up:
                    message = emojize(
                        f"🟢 {current_time_str} Світло з'явилося\n"
                        f"🕓 Його не було {duration_str}"
                    )
                    if event_info and event_type == 'outage':
                        next_outage_start = event_info['start'].strftime(
                            '%H:%M')
                        next_outage_end = event_info['end'].strftime('%H:%M')
                        next_event_str = f"{
                            next_outage_start} - {next_outage_end}"
                        message += emojize(
                            f"\n🗓 Наступне планове: {next_event_str}")
                else:
                    message = emojize(
                        f"🔴 {current_time_str} Світло зникло\n"
                        f"🕓 Воно було {duration_str}"
                    )
                    if event_info and event_type == 'available':
                        expected_return_time = event_info['start'].strftime(
                            '%H:%M')
                        next_event_str = f"о {expected_return_time}"
                        message += emojize(
                            f"\n🗓 Очікуємо за графіком {next_event_str}")

                self.send_message(message)
                logging.info(message)

            time.sleep(self.check_interval)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--token", type=str, required=True, help="Telegram Bot API token"
    )
    parser.add_argument(
        "--host", type=str, default="8.8.8.8", help="Host IP or domain to monitor"
    )
    parser.add_argument(
        "--chat-ids",
        nargs="+",
        type=int,
        required=True,
        help="List of chat IDs to send messages to",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Host check interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--db-file",
        type=str,
        default="state.db",
        help="SQLite database file (default: state.db)",
    )
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
