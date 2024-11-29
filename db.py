# db.py

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import psycopg
from loguru import logger
from psycopg.conninfo import make_conninfo

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, UTC_PLUS_2


def connect_to_db() -> psycopg.Connection:
    """Establish and return a database connection."""
    conninfo = make_conninfo(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )
    return psycopg.connect(conninfo)


def execute_query(query: str, params=None, fetch: bool = False):
    """Execute a database query."""
    try:
        with connect_to_db() as conn, conn.cursor() as cur:
            cur.execute(query, params or ())
            if fetch:
                return cur.fetchall()
            conn.commit()
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        return None


def init_host_status_table():
    """Initialize the host_status table."""
    query = """
        CREATE TABLE IF NOT EXISTS host_status (
            id BIGSERIAL PRIMARY KEY,
            status BOOLEAN NOT NULL DEFAULT TRUE,
            time TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """
    execute_query(query)
    logger.info("host_status table initialized.")


def init_outage_schedule_table():
    """Initialize the outage_schedule table."""
    query = """
        CREATE TABLE IF NOT EXISTS outage_schedule (
            id BIGSERIAL PRIMARY KEY,
            time TIMESTAMPTZ NOT NULL UNIQUE,
            registry_update_time TIMESTAMPTZ NOT NULL
        )
    """
    execute_query(query)
    logger.info("outage_schedule table initialized.")


def save_status(status: bool):
    """Save the current status."""
    execute_query(
        "INSERT INTO host_status (status, time) VALUES (%s, %s)",
        (status, datetime.now(UTC_PLUS_2)),
    )
    logger.info(f"Status {'UP' if status else 'DOWN'} saved.")


def get_last_status() -> Optional[bool]:
    """Get the most recent status."""
    result = execute_query(
        "SELECT status FROM host_status ORDER BY id DESC LIMIT 1", fetch=True
    )
    return result[0][0] if result else None


def get_total_time(previous_status: bool) -> Optional[timedelta]:
    """Calculate total time since the last status change."""
    result = execute_query(
        "SELECT time FROM host_status WHERE status = %s ORDER BY id DESC LIMIT 1",
        (not previous_status,),
        fetch=True,
    )
    if result:
        last_change_time = result[0][0]
        return datetime.now(UTC_PLUS_2) - last_change_time
    return None


def get_status_changes(start: datetime, end: datetime) -> List[Tuple[bool, datetime]]:
    """Retrieve status changes between two timestamps."""
    result = execute_query(
        "SELECT status, time FROM host_status WHERE time BETWEEN %s AND %s ORDER BY time",
        (start, end),
        fetch=True,
    )
    return result if result else []


def get_last_status_before(time_point: datetime) -> bool:
    """Get the last status before a specific time."""
    result = execute_query(
        "SELECT status FROM host_status WHERE time < %s ORDER BY time DESC LIMIT 1",
        (time_point,),
        fetch=True,
    )
    return result[0][0] if result else True


def check_schedule_updated(last_update_time: datetime) -> bool:
    """Check if the outage schedule was updated based on the last_update_time."""
    result = execute_query(
        "SELECT MAX(registry_update_time) FROM outage_schedule", fetch=True
    )
    if result and result[0][0]:
        latest_update_time = result[0][0]
        return last_update_time > latest_update_time
    return True  # No existing schedule, needs to update


def update_outage_schedule(schedule_entries: List[Tuple[datetime, datetime]]):
    """Update the outage schedule in the database."""
    try:
        with connect_to_db() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM outage_schedule WHERE time >= %s",
                (datetime.now(UTC_PLUS_2),),
            )
            insert_query = "INSERT INTO outage_schedule (time, registry_update_time) VALUES (%s, %s)"
            cur.executemany(insert_query, schedule_entries)
            conn.commit()
        logger.info("Outage schedule updated.")
    except Exception as e:
        logger.error(f"Error updating outage schedule: {e}")