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


def host_status_init():
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


def outage_schedule_init():
    """Initialize the outage_schedule table."""
    query = """
        CREATE TABLE IF NOT EXISTS outage_schedule (
            id BIGSERIAL PRIMARY KEY,
            time TIMESTAMPTZ NOT NULL UNIQUE
        )
    """
    execute_query(query)
    logger.info("outage_schedule table initialized.")


def host_status_save_status(status: bool):
    """Save the current status."""
    execute_query(
        "INSERT INTO host_status (status, time) VALUES (%s, %s)",
        (status, datetime.now(UTC_PLUS_2)),
    )
    logger.info(f"Status {'UP' if status else 'DOWN'} saved.")


def host_status_get_last_status() -> Optional[bool]:
    """Get the most recent status."""
    result = execute_query(
        "SELECT status FROM host_status ORDER BY id DESC LIMIT 1", fetch=True
    )
    return result[0][0] if result else None


def host_status_get_total_time(previous_status: bool) -> Optional[timedelta]:
    """Calculate total time since the last status change."""
    result = execute_query(
        "SELECT time FROM host_status WHERE status = %s ORDER BY id DESC LIMIT 1",
        (previous_status,),
        fetch=True,
    )
    return datetime.now(UTC_PLUS_2) - result[0][0] if result else None


def outage_schedule_outdated(schedule_entries: List[Tuple[datetime]]) -> bool:
    """Check if the fetched schedule differs from the existing schedule in the database."""
    result = execute_query(
        "SELECT time FROM outage_schedule WHERE time >= %s",
        (datetime.now(UTC_PLUS_2),),
        fetch=True
    )
    if result is None:
        return True
    return set(schedule_entries) != set(result)


def outage_schedule_update(schedule_entries: List[Tuple[datetime]]):
    """Update the outage schedule in the database."""
    try:
        with connect_to_db() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM outage_schedule WHERE time >= %s",
                (datetime.now(UTC_PLUS_2),),
            )
            insert_query = "INSERT INTO outage_schedule (time) VALUES (%s)"
            cur.executemany(insert_query, schedule_entries)
            conn.commit()
        logger.info("Outage schedule updated.")
    except Exception as e:
        logger.error(f"Error updating outage schedule: {e}")


def host_status_get_changes_between(start: datetime, end: datetime) -> List[Tuple[bool, datetime]]:
    """Retrieve status changes between two timestamps."""
    result = execute_query(
        "SELECT status, time FROM host_status WHERE time BETWEEN %s AND %s ORDER BY time",
        (start, end),
        fetch=True,
    )
    return result if result else []


def outage_schedule_get_between(start: datetime, end: datetime) -> List[Tuple[datetime]]:
    """Retrieve outage schedules between two timestamps."""
    result = execute_query(
        "SELECT time FROM outage_schedule WHERE time BETWEEN %s AND %s ORDER BY time",
        (start, end),
        fetch=True,
    )
    return result if result else []


def host_status_get_last_status_before(time_point: datetime) -> bool:
    """Get the last status before a specific time."""
    result = execute_query(
        "SELECT status FROM host_status WHERE time < %s ORDER BY time DESC LIMIT 1",
        (time_point,),
        fetch=True,
    )
    return result[0][0] if result else True
