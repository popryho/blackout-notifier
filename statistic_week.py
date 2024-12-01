# statistic_week.py

import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from loguru import logger

from config import UTC_PLUS_2
from db import (
    host_status_get_changes_between,
    host_status_get_last_status_before,
    host_status_init,
    outage_schedule_get_between,
    outage_schedule_init,
)
from tg import send_telegram_image

DAYS_OF_WEEK = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def split_events_by_day(
    start_time: datetime, events: List[Tuple[datetime, bool]]
) -> Dict[str, List[Tuple[datetime, bool]]]:
    """Split a list of events into daily intervals over a week starting from start_time."""
    intervals: Dict[str, List[Tuple[datetime, bool]]] = {
        day: [] for day in DAYS_OF_WEEK
    }

    for day_offset in range(7):
        day_start = start_time + timedelta(days=day_offset)
        day_end = day_start + timedelta(days=1)
        day_name = day_start.strftime("%A")

        # Get events that occur on this day
        day_events = [
            (timestamp, status)
            for timestamp, status in events
            if day_start <= timestamp < day_end
        ]

        # Determine the status at the start of the day
        prev_events = [
            (timestamp, status) for timestamp, status in events if timestamp < day_start
        ]
        status = prev_events[-1][1] if prev_events else events[0][1] if events else True

        intervals[day_name].append((day_start, status))
        logger.debug(f"{day_name:>9} | {int(status)} | {day_start}")

        for timestamp, status in day_events:
            intervals[day_name].append((timestamp, status))
            logger.debug(f"{day_name:>9} | {int(status)} | {timestamp}")

    return intervals


def host_status_get_intervals_by_day(
    start_time: datetime,
) -> Dict[str, List[Tuple[datetime, bool]]]:
    """Retrieve host status intervals for each day starting from start_time over a week."""
    status_at_start = host_status_get_last_status_before(start_time)
    all_changes = host_status_get_changes_between(
        start_time, start_time + timedelta(days=7)
    )
    events = [(start_time, status_at_start)] + [
        (timestamp.astimezone(UTC_PLUS_2), status) for timestamp, status in all_changes
    ]
    return split_events_by_day(start_time, events)


def merge_consecutive_outages(
    outage_times: List[datetime],
) -> List[Tuple[datetime, bool]]:
    """Merge consecutive outage times into intervals with start and end times."""
    merged = []
    if not outage_times:
        return merged

    outage_times.sort()
    current_start = outage_times[0]
    current_end = current_start + timedelta(hours=1)

    for ot in outage_times[1:]:
        if ot == current_end:
            current_end += timedelta(hours=1)
        else:
            merged.extend([(current_start, False), (current_end, True)])
            current_start = ot
            current_end = ot + timedelta(hours=1)
    merged.extend([(current_start, False), (current_end, True)])
    return merged


def outage_schedule_get_intervals_by_day(
    start_time: datetime,
) -> Dict[str, List[Tuple[datetime, bool]]]:
    """Retrieve scheduled outage intervals for each day starting from start_time over a week."""
    # Get all outage entries in the week
    outage_entries = outage_schedule_get_between(
        start_time, start_time + timedelta(days=7)
    )
    outage_times = [entry[0].astimezone(UTC_PLUS_2) for entry in outage_entries]

    # Merge consecutive outages
    merged_outages = merge_consecutive_outages(outage_times)

    # Determine status at start_time
    status_at_start = not any(ot == start_time for ot in outage_times)
    events = [(start_time, status_at_start)] + merged_outages

    return split_events_by_day(start_time, events)


def plot_weekly_intervals(
    actual_intervals: Dict[str, List[Tuple[datetime, bool]]],
    scheduled_intervals: Dict[str, List[Tuple[datetime, bool]]],
):
    """
    Plot two sets of weekly intervals as horizontal bar plots using matplotlib.
    """
    # Prepare data
    days = list(reversed(DAYS_OF_WEEK))
    day_to_num = {day: i for i, day in enumerate(days)}

    # Compute start_date and end_date from the intervals
    all_timestamps = []
    for intervals in [actual_intervals, scheduled_intervals]:
        for day_events in intervals.values():
            all_timestamps.extend([timestamp for timestamp, _ in day_events])

    if not all_timestamps:
        raise ValueError("No data to plot.")

    start_date = min(all_timestamps).date()
    end_date = max(all_timestamps).date() + timedelta(days=1)  # end_date is exclusive

    fig, ax = plt.subplots(figsize=(10, 5))
    bar_height = 0.3  # Thinner bar height

    def plot_intervals(intervals, offset, color_true, color_false):
        """
        Helper function to plot a list of intervals with specific offset and colors.
        """
        for day, day_intervals in intervals.items():
            day_num = day_to_num[day]
            for idx, (start, flag) in enumerate(day_intervals):
                # Start time since midnight
                start_of_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
                start_time = (start - start_of_day).total_seconds() / 3600  # in hours

                # Compute duration
                if idx + 1 < len(day_intervals):
                    end_time = day_intervals[idx + 1][0]
                    duration = (end_time - start).total_seconds() / 3600
                else:
                    duration = 24 - start_time

                color = color_true if flag else color_false
                ax.broken_barh(
                    [(start_time, duration)],
                    (day_num + offset, bar_height),
                    facecolors=color,
                    edgecolor="black",
                    linewidth=1.5,
                    alpha=1,
                )

    # Plot actual intervals
    plot_intervals(
        actual_intervals,
        offset=0.0,
        color_true="darkgreen",
        color_false="orangered",
    )

    # Plot scheduled intervals
    plot_intervals(
        scheduled_intervals,
        offset=-0.3,
        color_true="lightgreen",
        color_false="lightsalmon",
    )

    ax.set_xlim(0, 24)
    ax.set_ylim(-0.5, 6.5)

    # Primary axis ticks with longer ticks
    ticks_positions = [x for x in range(0, 25)]
    ax.set_xticks(ticks_positions)
    ax.set_xticklabels(["" for _ in range(0, 25)])  # Remove primary labels
    ax.tick_params(axis="x", length=12, width=1.5, color="grey")

    # Secondary X-axis labels
    secondary_x = ax.secondary_xaxis("bottom")
    secondary_x.set_xticks([x + 0.5 for x in range(0, 24)])  # Centered labels
    secondary_x.set_xticklabels(
        [f"{hour:02d}" for hour in range(0, 24)], fontweight="bold", fontsize=10
    )
    secondary_x.tick_params(axis="x", length=0)

    # Primary Y-axis ticks and labels
    ticks_positions = [
        -0.3,
        0.3,
        0.7,
        1.3,
        1.7,
        2.3,
        2.7,
        3.3,
        3.7,
        4.3,
        4.7,
        5.3,
        5.7,
        6.3,
    ]
    ax.set_yticks(ticks_positions)
    # Remove primary labels
    ax.set_yticklabels(["" for _ in range(len(ticks_positions))])
    ax.tick_params(axis="y", length=20, width=1.5)

    # Secondary Y-axis labels
    days_short = ["НД", "СБ", "ПТ", "ЧТ", "СР", "ВТ", "ПН"]
    secondary_y = ax.secondary_yaxis("left")
    secondary_y.set_yticks(range(7))
    secondary_y.set_yticklabels(days_short, fontsize=10, fontweight="bold")
    secondary_y.tick_params(axis="y", length=0)

    # Title and grid
    date_format = "%d.%m.%Y"
    start_str = start_date.strftime(date_format)
    end_str = (end_date - timedelta(days=1)).strftime(date_format)
    ax.set_title(
        f"статистика відключень світла за {start_str} - {end_str}",
        fontsize=14,
        fontweight="bold",
    )
    ax.grid(True, axis="x", linestyle="--", alpha=0.7, linewidth=1.5)

    # Create custom legend
    legend_patches = [
        mpatches.Patch(color="darkgreen", label="світло було"),
        mpatches.Patch(color="orangered", label="світла не було"),
        mpatches.Patch(color="lightgreen", label="світло мало бути"),
        mpatches.Patch(color="lightsalmon", label="світла не мало бути"),
    ]
    ax.legend(handles=legend_patches, loc="upper right")

    # Remove all borders
    for spine in ax.spines.values():
        spine.set_visible(False)

    plt.tight_layout()
    plt.savefig("weekly_intervals.png")
    plt.close()


def main():
    host_status_init()
    outage_schedule_init()

    now = datetime.now(UTC_PLUS_2)
    start_of_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    actual_intervals = host_status_get_intervals_by_day(start_of_week)
    scheduled_intervals = outage_schedule_get_intervals_by_day(start_of_week)

    plot_weekly_intervals(actual_intervals, scheduled_intervals)

    send_telegram_image("weekly_intervals.png")
    logger.info("Weekly power outage statistics sent to Telegram.")

    os.remove("weekly_intervals.png")


if __name__ == "__main__":
    main()
