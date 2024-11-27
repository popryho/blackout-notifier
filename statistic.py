import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot
from emoji import emojize

logging.basicConfig(level=logging.INFO)

UTC_PLUS_2 = timezone(timedelta(hours=2))


async def send_daily_statistics(bot_token: str, chat_id: int, db_file: str):
    bot = Bot(token=bot_token)
    async with aiosqlite.connect(db_file) as db:
        now_local = datetime.now(UTC_PLUS_2)
        yesterday_local = now_local - timedelta(days=1)
        start_of_day_local = datetime.combine(
            yesterday_local.date(), datetime.min.time(), tzinfo=UTC_PLUS_2
        )
        end_of_day_local = datetime.combine(
            yesterday_local.date(), datetime.max.time(), tzinfo=UTC_PLUS_2
        )

        # Convert local start and end times to UTC for querying the database
        start_of_day_utc = start_of_day_local.astimezone(timezone.utc)
        end_of_day_utc = end_of_day_local.astimezone(timezone.utc)

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

        # If no data for the previous day
        if not rows:
            message = emojize(
                f"💡Статистика за вчора ({yesterday_local.strftime('%Y-%m-%d')}):\n\n"
                "Немає даних за вчора."
            )
            await bot.send_message(chat_id, message)
            await bot.session.close()
            return

        total_on_time = timedelta()
        total_off_time = timedelta()

        # Initialize with the start of the day
        previous_time = start_of_day_utc
        previous_status = rows[0][0]

        for status, time_str in rows:
            current_time = datetime.fromisoformat(time_str)
            duration = current_time - previous_time
            print(status, time_str, duration)

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
        date_str = yesterday_local.strftime('%Y-%m-%d')
        message_header = emojize(f"💡Статистика за вчора ({date_str}):\n")

        if total_off_time == timedelta():
            message_body = emojize("\n🥳Електрика була увесь день!")
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
