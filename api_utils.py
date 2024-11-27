import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import requests

UTC_PLUS_2 = timezone(timedelta(hours=2))


class APIUtils:
    def __init__(self, group_id: str):
        self.group_id = group_id

    def get_next_event(self, is_up: bool) -> Tuple[Optional[str], Optional[dict]]:
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
