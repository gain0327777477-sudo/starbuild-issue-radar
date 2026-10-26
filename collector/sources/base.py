"""모든 수집 소스가 공통으로 쓰는 틀."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..config import Config
from ..http import http_get
from ..models import Article


class SkipSource(Exception):
    """이번 회차에는 건너뜀(키 없음, 간격이 아직 안 됨, 꺼짐 등). 실패가 아님."""


@dataclass
class Context:
    cfg: Config
    state: dict                  # 회차 사이에 남겨두는 기록(data/state.json)
    now: datetime
    get: Callable = http_get     # 테스트에서는 가짜 응답으로 바꿔 끼움


@dataclass
class SourceResult:
    articles: list[Article] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 일부 검색어만 실패한 경우 등
    calls: int = 0                                   # 실제로 보낸 요청 수
    extra: dict = field(default_factory=dict)        # 블로그 글 목록처럼 기사 아닌 결과
