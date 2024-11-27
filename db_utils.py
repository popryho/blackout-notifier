import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple


class DBUtils:
    def __init__(self, db_file: str):
        self.db_file = db_file

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

    def get_status_changes(self, start_time: datetime, end_time: datetime) -> List[Tuple[int, str]]:
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT status, time FROM host_status
                WHERE time BETWEEN ? AND ?
                ORDER BY time ASC
                """,
                (start_time.isoformat(), end_time.isoformat()),
            )
            rows = cursor.fetchall()
        return rows

    def get_last_status_before(self, time: datetime) -> int:
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT status FROM host_status
                WHERE time < ?
                ORDER BY time DESC
                LIMIT 1
                """,
                (time.isoformat(),),
            )
            row = cursor.fetchone()
        return row[0] if row else 1  # Default to 1 (on) if no previous status
