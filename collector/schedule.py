"""수집 일정 계산 — 한국 시간 정각 예약(config/sources.yaml 의 schedule_kst_hours).

GitHub cron 은 UTC 라서 워크플로에는 9시간을 뺀 시각이 적혀 있다. 둘이 같은지 확인하는 도구도 여기 있다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import KST

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "collect.yml"


def kst_to_utc_hours(hours: list[int]) -> list[int]:
    return sorted((h - 9) % 24 for h in hours)


def cron_kst_hours(cron: str) -> list[int] | None:
    """'0 1,3,13,21 * * *' → 한국 시간 [6, 10, 12, 22]. 분이 0 이 아니거나 형식이 다르면 None."""
    parts = cron.split()
    if len(parts) != 5 or parts[0] != "0" or parts[2:] != ["*", "*", "*"]:
        return None
    try:
        utc = [int(x) for x in parts[1].split(",")]
    except ValueError:
        return None
    return sorted((h + 9) % 24 for h in utc)


def workflow_cron(path: Path = WORKFLOW) -> str | None:
    if not path.exists():
        return None
    m = re.search(r'-\s*cron:\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _at(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def scheduled_slot(t: datetime, hours: list[int]) -> datetime:
    """t 이전(같거나 이른) 가장 최근 예약 시각. 새벽(06시 전)이면 전날 마지막 예약."""
    t = t.astimezone(KST)
    past = [h for h in sorted(hours) if _at(t, h) <= t]
    if past:
        return _at(t, past[-1])
    return _at(t - timedelta(days=1), max(hours))


def next_run(t: datetime, hours: list[int]) -> datetime:
    t = t.astimezone(KST)
    later = [h for h in sorted(hours) if _at(t, h) > t]
    return _at(t, later[0]) if later else _at(t + timedelta(days=1), min(hours))


def is_quiet(t: datetime, quiet: dict) -> bool:
    """밤 휴식 시간인가 (start 22, end 7 → 22:00~06:59)."""
    h = t.astimezone(KST).hour
    s, e = quiet["start"], quiet["end"]
    return (s <= h or h < e) if s > e else (s <= h < e)
