import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot
from emoji import emojize

logging.basicConfig(level=logging.INFO)


async def send_daily_statistics(bot_token: str, chat_id: int, db_file: str):
    bot = Bot(token=bot_token)
    async with aiosqlite.connect(db_file) as db:
        # Get current time in UTC
        now_utc = datetime.now(timezone.utc)
        yesterday_utc = now_utc - timedelta(days=1)
        start_of_day_utc = datetime.combine(
            yesterday_utc.date(), datetime.min.time(), tzinfo=timezone.utc
        )
        end_of_day_utc = datetime.combine(
            yesterday_utc.date(), datetime.max.time(), tzinfo=timezone.utc
        )

        # Fetch status changes for the previous day
        async with db.execute(
            """
            SELECT status, time FROM host_status
            WHERE time BETWEEN ? AND ?
            ORDER BY time ASC
            """,
            (start_of_day_utc.isoformat(), end_of_day_utc.isoformat()),
        ) as cursor:
            rows = await cursor.fetchall()

        # Get the last status before the start of the day
        async with db.execute(
            """
            SELECT status FROM host_status
            WHERE time < ?
            ORDER BY time DESC
            LIMIT 1
            """,
            (start_of_day_utc.isoformat(),),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                previous_status = row[0]
            else:
                previous_status = 1  # Adjust if your default is different

        total_on_time = timedelta()
        total_off_time = timedelta()
        previous_time = start_of_day_utc

        if not rows:
            # No status changes during the day
            duration = end_of_day_utc - previous_time
            if previous_status:
                total_on_time = duration
            else:
                total_off_time = duration
        else:
            for status, time_str in rows:
                current_time = datetime.fromisoformat(time_str)
                duration = current_time - previous_time

                if previous_status:
                    total_on_time += duration
                else:
                    total_off_time += duration

                previous_time = current_time
                previous_status = status

            # Handle the time from the last record to the end of the day
            duration = end_of_day_utc - previous_time
            if previous_status:
                total_on_time += duration
            else:
                total_off_time += duration

        # Format durations
        def format_duration(td):
            total_seconds = int(td.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return f"{hours} год. {minutes} хв."

        total_on_str = format_duration(total_on_time)
        total_off_str = format_duration(total_off_time)

        # Prepare the message
        date_str = (now_utc - timedelta(days=1)).strftime('%Y-%m-%d')
        message_header = emojize(f"💡Статистика за вчора ({date_str}):\n")

        if total_off_time == timedelta():
            message_body = emojize("\n🥳Електрика була увесь день!")
        elif total_on_time == timedelta():
            message_body = emojize("\n😞Електрика була відсутня весь день.")
        else:
            message_body = emojize(
                f"\n✅Електрика присутня: {total_on_str}.\n"
                f"❌Електрика відсутня: {total_off_str}."
            )

        message = message_header + message_body

        # Send the message
        await bot.send_message(chat_id, message)
        await bot.session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Send daily electricity statistics.")
    parser.add_argument("--token", required=True,
                        help="Telegram Bot API token")
    parser.add_argument("--chat-id", required=True,
                        type=int, help="Telegram Chat ID")
    parser.add_argument("--db-file", default="state.db",
                        help="SQLite database file")
    args = parser.parse_args()

    asyncio.run(send_daily_statistics(args.token, args.chat_id, args.db_file))


if __name__ == "__main__":
    main()
