"""수집한 기사 한 건의 모양과 시간·글자 정리 도구."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

KST = timezone(timedelta(hours=9))   # 한국은 서머타임이 없어 고정 +9시간


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def parse_rfc822(value: str) -> datetime | None:
    """RSS·네이버 API의 'Mon, 26 Oct 2026 09:44:52 +0900' 형식을 한국 시간으로."""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def clean_text(value: str | None) -> str:
    """HTML 태그(<b> 등)와 &quot; 같은 기호를 걷어낸 일반 글자."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", "", value)
    text = html.unescape(html.unescape(text))
    return re.sub(r"\s+", " ", text).strip()


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def article_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


@dataclass
class Article:
    id: str
    title: str
    url: str              # 원문 링크 (네이버는 originallink)
    press: str            # 언론사·기관 이름
    domain: str           # 원문 주소의 도메인 (해외 매체 거르기에 사용)
    source: str           # naver / google / briefing / local_gov
    published: str        # 발행 시각 (한국 시간, ISO 형식)
    summary: str          # 소스가 주는 짧은 요약만 (본문은 저장하지 않음)
    query: str            # 어떤 검색어로 찾았는지
    field: str | None     # 검색어가 속한 분야 (분류 참고용)
    fetched_at: str
    agency: str = ""      # 정책브리핑: 부처 이름
    region_hint: str = "" # 지자체 보도자료: 시·도 이름

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        known = {k: d.get(k, "") for k in cls.__dataclass_fields__}
        return cls(**known)
