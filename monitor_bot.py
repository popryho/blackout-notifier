import argparse
import asyncio
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

import aiosqlite
from aiogram import Bot, Dispatcher
from emoji import emojize

logging.basicConfig(level=logging.INFO)


class Monitor:
    def __init__(self, bot: Bot, host: str, chat_ids: List[int], check_interval: int, db_file: str):
        self.bot = bot
        self.host = host
        self.chat_ids = chat_ids
        self.check_interval = check_interval
        self.db_file = db_file
        self.last_host_status: Optional[bool] = None
        self.status_change_time: Optional[datetime] = None

    async def init_db(self):
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS host_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status INTEGER NOT NULL,
                    time TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def save_status(self, status: bool):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                "INSERT INTO host_status (status, time) VALUES (?, ?)", (int(
                    status), now)
            )
            await db.commit()

    async def get_last_status(self) -> Tuple[Optional[bool], Optional[datetime]]:
        async with aiosqlite.connect(self.db_file) as db:
            async with db.execute(
                "SELECT status, time FROM host_status ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    status = bool(row[0])
                    time = datetime.fromisoformat(row[1])
                    return status, time
                else:
                    return None, None

    async def get_total_time(self, current_status: bool) -> Optional[timedelta]:
        async with aiosqlite.connect(self.db_file) as db:
            async with db.execute(
                "SELECT time FROM host_status WHERE status = ? ORDER BY id DESC LIMIT 1",
                (int(not current_status),),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    last_change_time = datetime.fromisoformat(row[0])
                    total_time = datetime.now(timezone.utc) - last_change_time
                    return total_time
                else:
                    return None

    async def ping_host(self) -> bool:
        proc = await asyncio.create_subprocess_shell(
            f"ping -c 20 -W 1 {self.host}",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return_code = await proc.wait()
        return return_code == 0

    async def send_message(self, message: str):
        for chat_id in self.chat_ids:
            await self.bot.send_message(chat_id, message)

    async def monitor(self):
        await self.init_db()
        self.last_host_status, self.status_change_time = await self.get_last_status()

        while True:
            try:
                is_up = await self.ping_host()
            except Exception as e:
                logging.error(f"Error pinging host: {e}")
                is_up = False

            if self.last_host_status is None:
                self.last_host_status = is_up
                await self.save_status(is_up)
                self.status_change_time = datetime.now(timezone.utc)
                status_str = 'UP' if is_up else 'DOWN'
                message = f"Host {self.host} initial status is {status_str}"
                logging.info(message)
            elif is_up != self.last_host_status:
                await self.save_status(is_up)
                total_time = await self.get_total_time(is_up)
                self.last_host_status = is_up
                self.status_change_time = datetime.now(timezone.utc)

                current_time = datetime.now().strftime('%H:%M')
                if total_time:
                    hours, remainder = divmod(
                        int(total_time.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    duration_str = f"{hours} год. {minutes} хв."
                else:
                    duration_str = "невідомо"

                if is_up:
                    message = emojize(
                        f":check_mark_button: Світло з'явилось\n"
                        f":alarm_clock: Час: {current_time}\n\n"
                        f":new_moon_face: Було відсутнім: {duration_str}"
                    )
                else:
                    message = emojize(
                        f":cross_mark: Світло зникло\n"
                        f":alarm_clock: Час: {current_time}\n\n"
                        f":full_moon_face: Було присутнім: {duration_str}"
                    )
                await self.send_message(message)
                logging.info(message)

            await asyncio.sleep(self.check_interval)


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


async def main():
    args = create_parser().parse_args()
    bot = Bot(token=args.token)
    monitor = Monitor(
        bot=bot,
        host=args.host,
        chat_ids=args.chat_ids,
        check_interval=args.interval,
        db_file=args.db_file
    )
    asyncio.create_task(monitor.monitor())
    dp = Dispatcher()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
