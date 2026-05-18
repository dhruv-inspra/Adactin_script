from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BUSINESS_START = time(0, 0)
BUSINESS_END = time(23, 59, 59, 999999)
BUSINESS_DAYS = set(range(7))
BUSINESS_HOURS_LABEL = "Daily 00:00-24:00"
DEFAULT_TIMEZONE = "Asia/Kolkata"
TIMEZONE_ALIASES = {
    "Melbourne, Australia": "Australia/Melbourne",
    "Melbourne": "Australia/Melbourne",
    "Sydney, Australia": "Australia/Sydney",
    "Sydney": "Australia/Sydney",
}
TIMEZONE_FALLBACKS = {
    "Australia/Melbourne": timezone(timedelta(hours=10), "Australia/Melbourne"),
    "Australia/Sydney": timezone(timedelta(hours=10), "Australia/Sydney"),
}


def dialing_timezone():
    raw_name = os.environ.get("DIALING_TIMEZONE") or os.environ.get("BUSINESS_TIMEZONE") or DEFAULT_TIMEZONE
    name = TIMEZONE_ALIASES.get(raw_name, raw_name)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Kolkata", "Asia/Calcutta", DEFAULT_TIMEZONE}:
            return timezone(timedelta(hours=5, minutes=30), name)
        if name in TIMEZONE_FALLBACKS:
            return TIMEZONE_FALLBACKS[name]
        raise


def current_dialing_window(now: datetime | None = None) -> dict[str, Any]:
    local_now = _local_now(now)
    scheduled_for = next_dialing_time(local_now)
    is_open = local_now == scheduled_for
    return {
        "is_open": is_open,
        "now": local_now.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "timezone": str(local_now.tzinfo),
        "business_hours": BUSINESS_HOURS_LABEL,
    }


def next_dialing_time(now: datetime | None = None) -> datetime:
    local_now = _local_now(now)
    candidate = local_now

    if candidate.weekday() not in BUSINESS_DAYS:
        days_until_monday = (7 - candidate.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 1
        return _at_business_start(candidate + timedelta(days=days_until_monday))

    if candidate.time() < BUSINESS_START:
        return _at_business_start(candidate)

    if candidate.time() >= BUSINESS_END:
        return _next_business_day_start(candidate)

    return candidate


def queue_call_payloads(
    payloads: list[dict[str, Any]],
    *,
    scheduled_for: str,
    reason: str,
    queue_file: Path | None = None,
) -> Path:
    path = queue_file or Path(os.environ.get("CALL_QUEUE_FILE", "outputs/call_queue.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(
                json.dumps(
                    {
                        "queued_at": datetime.now(dialing_timezone()).isoformat(),
                        "scheduled_for": scheduled_for,
                        "reason": reason,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return path


def _local_now(now: datetime | None = None) -> datetime:
    tz = dialing_timezone()
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _at_business_start(value: datetime) -> datetime:
    return value.replace(hour=BUSINESS_START.hour, minute=0, second=0, microsecond=0)


def _next_business_day_start(value: datetime) -> datetime:
    candidate = value + timedelta(days=1)
    while candidate.weekday() not in BUSINESS_DAYS:
        candidate += timedelta(days=1)
    return _at_business_start(candidate)
