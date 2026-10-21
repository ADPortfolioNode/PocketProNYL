"""Which draw a suggestion served right now is for.

Uses ``GAME_PREDICTION_SCHEDULES`` from config (draw days, and for twice-daily
games the session times). For games whose draw time is not in config, the
published time is used where known; otherwise the target is the next draw day
strictly after today, so a suggestion can never be graded against a draw that
had already happened when it was served.

Published draw times (ET):
  Powerball 10:59 pm (Mon/Wed/Sat)  https://www.powerball.com/
  Mega Millions 11:00 pm (Tue/Fri)  https://www.megamillions.com/How-to-Play.aspx
  Millionaire for Life 11:15 pm     https://www.powerball.com/millionaire-for-life
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import GAME_PREDICTION_SCHEDULES

_PUBLISHED_DRAW_TIMES = {
    "powerball": "22:59",
    "megamillions": "23:00",
    "millionaireforlife": "23:15",
}


def lottery_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("PREDICTION_TIMEZONE", "America/New_York"))
    except Exception:
        return ZoneInfo("America/New_York")


def _draws_on(schedule: dict, day: date) -> int:
    weekday_draws = schedule.get("weekday_draws") or {}
    if weekday_draws:
        return int(weekday_draws.get(day.weekday(), weekday_draws.get(str(day.weekday()), 0)) or 0)
    return int(schedule.get("daily_draws", 1) or 0)


def _parse_hhmm(value: str) -> time | None:
    try:
        hours, minutes = str(value).split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def next_draw(game: str, now: datetime | None = None) -> dict:
    """Return ``{"draw_date", "draw_session", "draw_time", "basis"}`` for the next draw."""
    tz = lottery_timezone()
    now = now.astimezone(tz) if now and now.tzinfo else (now.replace(tzinfo=tz) if now else datetime.now(tz))
    schedule = dict(GAME_PREDICTION_SCHEDULES.get(game, {}) or {})
    times = [t for t in (_parse_hhmm(v) for v in schedule.get("draw_times") or []) if t]
    sessions = list(schedule.get("draw_sessions") or [])
    if not times and game in _PUBLISHED_DRAW_TIMES:
        times = [_parse_hhmm(_PUBLISHED_DRAW_TIMES[game])]
        sessions = [None]
    for offset in range(0, 15):
        day = now.date() + timedelta(days=offset)
        if _draws_on(schedule, day) <= 0:
            continue
        if times:
            for index, draw_time in enumerate(times):
                draw_dt = datetime.combine(day, draw_time, tzinfo=tz)
                if draw_dt > now:
                    session = sessions[index] if index < len(sessions) else None
                    return {
                        "draw_date": day.isoformat(),
                        "draw_session": session if len(times) > 1 else None,
                        "draw_time": draw_dt.isoformat(),
                        "basis": "schedule_time",
                    }
            continue
        if offset == 0:
            # Draw time unknown: today's draw may already be over, so target the next day.
            continue
        return {"draw_date": day.isoformat(), "draw_session": None, "draw_time": None,
                "basis": "next_draw_day_after_today"}
    return {"draw_date": None, "draw_session": None, "draw_time": None, "basis": "unknown"}
