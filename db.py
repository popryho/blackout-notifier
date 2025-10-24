# db.py

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import psycopg
from loguru import logger
from psycopg.conninfo import make_conninfo
from psycopg.errors import OperationalError

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")


@dataclass
class DatabaseConfig:
    """Database configuration."""

    host: str
    port: int
    database: str
    user: str
    password: str

    def __post_init__(self):
        """Validate database configuration."""
        if not all([self.host, self.database, self.user, self.password]):
            raise ValueError("All database configuration fields are required")
        if not (1 <= self.port <= 65535):
            raise ValueError("Port must be between 1 and 65535")


class TableType(Enum):
    """Enum for different table types."""

    HOST_STATUS = "host_status"
    OUTAGE_SCHEDULE = "outage_schedule"
    SCHEDULE_UPDATE_TRACKER = "schedule_update_tracker"


class DatabaseManager:
    """Manages database connections and operations."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection_string = self._build_connection_string()

    def _build_connection_string(self) -> str:
        """Build the database connection string."""
        return make_conninfo(
            dbname=self.config.database,
            user=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
        )

    @contextmanager
    def get_connection(self):
        """Get a database connection with proper error handling."""
        conn = None
        try:
            conn = psycopg.connect(self._connection_string)
            yield conn
        except OperationalError as e:
            logger.error(f"Database connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected database error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_query(
        self, query: str, params: Optional[Tuple] = None, fetch: bool = False
    ) -> Optional[List[Tuple]]:
        """Execute a database query with proper error handling."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params or ())
                    if fetch:
                        return cur.fetchall()
                    conn.commit()
                    return None
        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise

    def execute_transaction(self, queries: List[Tuple[str, Optional[Tuple]]]) -> None:
        """Execute multiple queries in a transaction."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for query, params in queries:
                        cur.execute(query, params or ())
                    conn.commit()
        except Exception as e:
            logger.error(f"Transaction execution error: {e}")
            raise


# Global database manager instance
_db_manager = None


def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        config = DatabaseConfig(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        _db_manager = DatabaseManager(config)
    return _db_manager


class BaseRepository:
    """Base repository class for database operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager


class HostStatusRepository(BaseRepository):
    """Repository for host status operations."""

    def __init__(self, db_manager: DatabaseManager):
        super().__init__(db_manager)
        self.table_name = TableType.HOST_STATUS.value

    def initialize_table(self) -> None:
        """Initialize the host_status table."""
        query = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id BIGSERIAL PRIMARY KEY,
                status BOOLEAN NOT NULL DEFAULT TRUE,
                time TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        self.db_manager.execute_query(query)
        logger.info(f"{self.table_name} table initialized.")

    def save_status(self, status: bool) -> None:
        """Save the current status."""
        query = f"INSERT INTO {self.table_name} (status, time) VALUES (%s, %s)"
        self.db_manager.execute_query(query, (status, datetime.now(KYIV_TIMEZONE)))
        logger.info(f"Status {'UP' if status else 'DOWN'} saved.")

    def get_last_status(self) -> Optional[bool]:
        """Get the most recent status."""
        query = f"SELECT status FROM {self.table_name} ORDER BY id DESC LIMIT 1"
        result = self.db_manager.execute_query(query, fetch=True)
        return result[0][0] if result else None

    def get_total_time(self, previous_status: bool) -> Optional[timedelta]:
        """Calculate total time since the last status change."""
        query = f"SELECT time FROM {self.table_name} WHERE status = %s ORDER BY id DESC LIMIT 1"
        result = self.db_manager.execute_query(query, (previous_status,), fetch=True)
        return datetime.now(KYIV_TIMEZONE) - result[0][0] if result else None

    def get_changes_between(
        self, start: datetime, end: datetime
    ) -> List[Tuple[datetime, bool]]:
        """Retrieve status changes between two timestamps."""
        query = f"SELECT time, status FROM {self.table_name} WHERE time BETWEEN %s AND %s ORDER BY time"
        result = self.db_manager.execute_query(query, (start, end), fetch=True)
        return result if result else []

    def get_last_status_before(self, time_point: datetime) -> bool:
        """Get the last status before a specific time."""
        query = f"SELECT status FROM {self.table_name} WHERE time < %s ORDER BY time DESC LIMIT 1"
        result = self.db_manager.execute_query(query, (time_point,), fetch=True)
        return result[0][0] if result else True


class OutageScheduleRepository(BaseRepository):
    """Repository for outage schedule operations."""

    def __init__(self, db_manager: DatabaseManager):
        super().__init__(db_manager)
        self.table_name = TableType.OUTAGE_SCHEDULE.value

    def initialize_table(self) -> None:
        """Initialize the outage_schedule table."""
        query = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id BIGSERIAL PRIMARY KEY,
                status BOOLEAN NOT NULL DEFAULT TRUE,
                time TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        self.db_manager.execute_query(query)
        logger.info(f"{self.table_name} table initialized.")

    def clear_schedule_for_date(self, date: datetime.date) -> None:
        """Delete all schedule entries for a specific date."""
        start_of_day = datetime.combine(date, datetime.min.time(), tzinfo=KYIV_TIMEZONE)
        end_of_day = start_of_day + timedelta(days=1)

        query = f"DELETE FROM {self.table_name} WHERE time >= %s AND time < %s"
        self.db_manager.execute_query(query, (start_of_day, end_of_day))
        logger.debug(f"Cleared schedule entries for {date}")

    def insert_schedule_entries(
        self, schedule_entries: List[Tuple[bool, datetime]]
    ) -> None:
        """Insert schedule entries into the database."""
        if not schedule_entries:
            logger.debug("No schedule entries to insert.")
            return

        query = f"INSERT INTO {self.table_name} (status, time) VALUES (%s, %s)"

        with self.db_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(query, schedule_entries)
                conn.commit()

        logger.debug(f"Inserted {len(schedule_entries)} schedule entries.")

    def get_schedule_between(
        self, start: datetime, end: datetime
    ) -> List[Tuple[datetime, bool]]:
        """Retrieve outage schedules between two timestamps."""
        query = f"SELECT time, status FROM {self.table_name} WHERE time BETWEEN %s AND %s ORDER BY time"
        result = self.db_manager.execute_query(query, (start, end), fetch=True)
        return result if result else []


class ScheduleUpdateTrackerRepository(BaseRepository):
    """Repository for schedule update tracker operations."""

    def __init__(self, db_manager: DatabaseManager):
        super().__init__(db_manager)
        self.table_name = TableType.SCHEDULE_UPDATE_TRACKER.value

    def initialize_table(self) -> None:
        """Initialize the schedule_update_tracker table."""
        query = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id BIGSERIAL PRIMARY KEY,
                last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        self.db_manager.execute_query(query)
        logger.info(f"{self.table_name} table initialized.")

    def has_schedule_changed(self, new_datetime_str: str) -> bool:
        """Check if schedule has changed since last check."""
        query = f"SELECT last_updated FROM {self.table_name} ORDER BY id DESC LIMIT 1"
        result = self.db_manager.execute_query(query, fetch=True)
        if not result:
            logger.info("No last updated datetime found in database.")
            return True

        new_datetime = datetime.fromisoformat(new_datetime_str)
        stored_datetime = result[0][0]
        if new_datetime != stored_datetime:
            logger.info("Schedule has changed since last check.")
            return True
        return False

    def save_last_updated_time(self, datetime_str: str) -> None:
        """Save the last updated datetime to tracker."""
        query = f"INSERT INTO {self.table_name} (last_updated) VALUES (%s)"
        self.db_manager.execute_query(query, (datetime_str,))
