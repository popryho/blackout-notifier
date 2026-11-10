# widget_week.py
"""Weekly outage statistics rendered as a Telegram HTML widget (Bot API 10.4).

The chart is pre-rendered here as plain HTML/CSS, so it is visible even if the
widget's script does not run; the script only switches the per-day details.
"""

import zlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

DAYS_OF_WEEK = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
DAYS_SHORT = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "НД"]

MINUTES_IN_DAY = 24 * 60
MAX_HTML_BYTES = 32768
MAX_COMPRESSED_BYTES = 4096
MAX_DETAIL_ITEMS = 8

Intervals = Dict[str, List[Tuple[datetime, bool]]]
Segment = Tuple[int, int, bool]  # (start minute, end minute, power is on)

CSS = (
    "body{margin:8px 12px;font-size:13px}"
    ".t{font-weight:600}"
    ".h{color:var(--tg-theme-hint-color,#888);font-size:12px}"
    ".r{display:flex;align-items:center;gap:6px;padding:3px 0}"
    ".r.s .d{color:var(--tg-theme-link-color,#2481cc)}"
    ".d{width:20px;font-weight:600;font-size:11px}"
    ".b{flex:1}"
    ".k{position:relative;overflow:hidden;border-radius:2px}"
    ".a{height:11px;background:#2e9e5b}"
    ".p{height:6px;margin-top:2px;background:light-dark(#bfe6cc,#2f5a40)}"
    ".k i{position:absolute;top:0;bottom:0}"
    ".a i{background:#e5484d}"
    ".p i{background:light-dark(#f4b8a4,#8a4234)}"
    ".o{width:34px;text-align:right;font-size:11px}"
    ".x{display:flex;margin:0 40px 0 26px;font-size:10px}"
    ".x span{flex:1}"
    ".l{margin:6px 0}"
    ".l b{display:inline-block;width:8px;height:8px;border-radius:2px;margin:0 3px 0 8px}"
    ".l b:first-child{margin-left:0}"
    "#i{min-height:54px;font-size:12px}"
)

JS = (
    "function s(n){var r=document.querySelectorAll('.r'),e=document.querySelectorAll('#i>div');"
    "for(var j=0;j<r.length;j++){r[j].classList.toggle('s',j==n);e[j+1].hidden=j!=n}"
    "e[0].hidden=true}"
)


def day_segments(day_intervals: List[Tuple[datetime, bool]]) -> List[Segment]:
    """Convert one day's (timestamp, status) events into minute-based segments."""
    segments: List[Segment] = []
    if not day_intervals:
        return segments

    day_start = day_intervals[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
    for idx, (start, flag) in enumerate(day_intervals):
        begin = int((start - day_start).total_seconds() // 60)
        if idx + 1 < len(day_intervals):
            end = int((day_intervals[idx + 1][0] - day_start).total_seconds() // 60)
        else:
            end = MINUTES_IN_DAY
        begin = max(0, min(begin, MINUTES_IN_DAY))
        end = max(0, min(end, MINUTES_IN_DAY))
        if end <= begin:
            continue
        if segments and segments[-1][2] == flag and segments[-1][1] == begin:
            segments[-1] = (segments[-1][0], end, flag)
        else:
            segments.append((begin, end, flag))
    return segments


def _off(segments: List[Segment]) -> List[Tuple[int, int]]:
    return [(begin, end) for begin, end, flag in segments if not flag]


def _total(periods: List[Tuple[int, int]]) -> int:
    return sum(end - begin for begin, end in periods)


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} год.")
    if mins > 0 or hours == 0:
        parts.append(f"{mins} хв.")
    return " ".join(parts)


def _pct(minutes: int) -> str:
    return f"{minutes * 100 / MINUTES_IN_DAY:.2f}".rstrip("0").rstrip(".")


def _track(css_class: str, periods: List[Tuple[int, int]]) -> str:
    bars = "".join(
        f'<i style="left:{_pct(begin)}%;width:{_pct(end - begin)}%"></i>'
        for begin, end in periods
    )
    return f'<div class="k {css_class}">{bars}</div>'


def _period_list(periods: List[Tuple[int, int]]) -> str:
    if not periods:
        return "—"
    shown = [
        f"{_clock(begin)}–{_clock(end)} ({_duration(end - begin)})"
        for begin, end in periods[:MAX_DETAIL_ITEMS]
    ]
    if len(periods) > MAX_DETAIL_ITEMS:
        shown.append(f"ще {len(periods) - MAX_DETAIL_ITEMS}")
    return ", ".join(shown)


def _render(
    start_of_week: datetime,
    actual: List[List[Tuple[int, int]]],
    planned: List[List[Tuple[int, int]]],
    with_details: bool,
) -> str:
    end_of_week = start_of_week + timedelta(days=6)
    actual_total = sum(_total(day) for day in actual)
    planned_total = sum(_total(day) for day in planned)

    rows = []
    details = []
    for idx in range(7):
        onclick = f' onclick="s({idx})"' if with_details else ""
        rows.append(
            f'<div class="r"{onclick}><span class="d">{DAYS_SHORT[idx]}</span>'
            f'<div class="b">{_track("a", actual[idx])}{_track("p", planned[idx])}</div>'
            f'<span class="o">{_total(actual[idx]) // 60}:{_total(actual[idx]) % 60:02d}</span></div>'
        )
        date_str = (start_of_week + timedelta(days=idx)).strftime("%d.%m")
        details.append(
            f"<div hidden><b>{DAYS_SHORT[idx]} {date_str}</b><br>"
            f"не було: {_period_list(actual[idx])}<br>"
            f'<span class="h">графік: {_period_list(planned[idx])}</span></div>'
        )

    axis = "".join(f"<span>{hour:02d}</span>" for hour in range(0, 24, 3))
    legend = (
        '<div class="l h"><b style="background:#2e9e5b"></b>було'
        '<b style="background:#e5484d"></b>не було'
        '<b style="background:light-dark(#f4b8a4,#8a4234)"></b>за графіком</div>'
    )
    info = ""
    script = ""
    if with_details:
        info = (
            '<div id="i"><div class="h">Натисніть на день, щоб побачити деталі</div>'
            + "".join(details)
            + "</div>"
        )
        script = f"<script>{JS}</script>"

    return (
        f"<style>{CSS}</style>"
        f'<div class="t">Відключення {start_of_week.strftime("%d.%m")} – '
        f'{end_of_week.strftime("%d.%m.%Y")}</div>'
        f'<div class="h">без світла {_duration(actual_total)} · '
        f"за графіком {_duration(planned_total)}</div>"
        f'{"".join(rows)}<div class="x h">{axis}</div>{legend}{info}{script}'
    )


def _fits(html: str) -> bool:
    raw = html.encode("utf-8")
    return (
        len(raw) <= MAX_HTML_BYTES and len(zlib.compress(raw, 9)) <= MAX_COMPRESSED_BYTES
    )


def build_weekly_widget(
    start_of_week: datetime,
    actual_intervals: Intervals,
    scheduled_intervals: Intervals,
) -> Tuple[Optional[str], str]:
    """Build (html, caption) for the weekly widget.

    html is None when the widget cannot fit into Telegram's size limits even
    without the per-day details.
    """
    actual = [_off(day_segments(actual_intervals.get(day, []))) for day in DAYS_OF_WEEK]
    planned = [
        _off(day_segments(scheduled_intervals.get(day, []))) for day in DAYS_OF_WEEK
    ]

    end_of_week = start_of_week + timedelta(days=6)
    caption = (
        f"Статистика відключень світла за {start_of_week.strftime('%d.%m.%Y')} - "
        f"{end_of_week.strftime('%d.%m.%Y')}\n"
        f"Без світла: {_duration(sum(_total(day) for day in actual))}\n"
        f"За графіком: {_duration(sum(_total(day) for day in planned))}"
    )

    for with_details in (True, False):
        html = _render(start_of_week, actual, planned, with_details)
        if _fits(html):
            return html, caption
    return None, caption
