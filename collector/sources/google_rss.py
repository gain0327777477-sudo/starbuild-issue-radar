"""Google News RSS 공통 도구 (정책브리핑·일반 검색·지자체 검색이 함께 씀)."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from ..http import SourceError
from ..models import Article, article_id, clean_text, domain_of, parse_rfc822
from .base import Context, SkipSource

URL = "https://news.google.com/rss/search"
BLOCK_STATUSES = (429, 503)
BLOCK_MINUTES = 60
BRIEFING_TAIL = re.compile(r"\s+-\s+[^-]{1,15}(\s\|\s[^-|]{1,15})+$")


def or_query(terms: list[str]) -> str:
    """['미국 증시', '나스닥'] → '("미국 증시" OR 나스닥)'"""
    quoted = [f'"{t}"' if " " in t else t for t in terms]
    return quoted[0] if len(quoted) == 1 else "(" + " OR ".join(quoted) + ")"


def parse_rss(xml_text: str, *, source: str, query: str, field: str | None, fetched_at: str,
              agency: str = "", region_hint: str = "") -> list[Article]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"RSS 형식이 아님(사이트 응답이 바뀌었을 수 있음): {e}; 응답 앞부분: {xml_text[:120]}")
    out = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        published = parse_rfc822(item.findtext("pubDate") or "")
        src = item.find("source")
        press = clean_text(src.text) if src is not None else ""
        press_url = src.get("url", "") if src is not None else ""
        if press and title.endswith(" - " + press):
            title = title[: -len(press) - 3].rstrip()
        title = BRIEFING_TAIL.sub("", title)       # '… - 정책뉴스 | 뉴스' 같은 꼬리말
        if not title or not link or not published:
            continue
        out.append(Article(
            id=article_id(link),
            title=title,
            url=link,                               # 구글 뉴스 주소 → 누르면 원문으로 이동
            press=press or "Google News",
            domain=domain_of(press_url) if press_url else "",
            source=source,
            published=published.isoformat(),
            summary="",                             # 구글 RSS 요약은 제목 반복이라 쓰지 않음
            query=query,
            field=field,
            fetched_at=fetched_at,
            agency=agency,
            region_hint=region_hint,
        ))
    return out


def check_not_blocked(ctx: Context) -> None:
    until = ctx.state.get("google_block_until")
    if until and ctx.now.isoformat() < until:
        raise SkipSource(f"구글이 잠시 막아서 쉬는 중 — {until[11:16]} 이후 다시 시도")


def mark_blocked(ctx: Context) -> str:
    until = (ctx.now + timedelta(minutes=BLOCK_MINUTES)).isoformat()
    ctx.state["google_block_until"] = until
    return until[11:16]


def fetch(ctx: Context, q: str) -> str:
    params = {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    # 2026-10-26 실측: 구글이 가끔 정상 검색어에도 404를 줬다가 다시 보내면 200 → 404도 한 번 더 시도
    # (결과 0건일 때는 404가 아니라 200 + 빈 목록으로 옴)
    r = ctx.get(URL, params=params, retry_statuses=(404,))
    time.sleep(ctx.cfg.sources["google_news"].get("pause_seconds", 1.5))
    return r.text
