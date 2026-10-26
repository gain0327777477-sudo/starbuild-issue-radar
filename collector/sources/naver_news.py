"""1. 네이버 검색 API(뉴스). 키가 없으면 건너뛴다."""
from __future__ import annotations

import time
from datetime import datetime

from ..env import get_secret, has_naver_keys
from ..http import SourceError
from ..models import Article, article_id, clean_text, domain_of, parse_rfc822
from ..press import press_name
from .base import Context, SkipSource, SourceResult

ID = "naver_news"
NAME = "네이버 뉴스"
URL = "https://openapi.naver.com/v1/search/news.json"


def parse_items(data: dict, query: str, field: str | None, fetched_at: str) -> list[Article]:
    out = []
    for it in data.get("items") or []:
        url = it.get("originallink") or it.get("link") or ""
        published = parse_rfc822(it.get("pubDate", ""))
        if not url or not published:
            continue
        domain = domain_of(url)
        out.append(Article(
            id=article_id(url),
            title=clean_text(it.get("title")),
            url=url,
            press=press_name(domain),
            domain=domain,
            source="naver",
            published=published.isoformat(),
            summary=clean_text(it.get("description"))[:160],
            query=query,
            field=field,
            fetched_at=fetched_at,
        ))
    return out


def _calls_today(ctx: Context) -> int:
    rec = ctx.state.get("naver_calls") or {}
    return rec.get("count", 0) if rec.get("date") == ctx.now.date().isoformat() else 0


def _add_calls(ctx: Context, n: int) -> None:
    ctx.state["naver_calls"] = {"date": ctx.now.date().isoformat(), "count": _calls_today(ctx) + n}


def per_keyword_now(ctx: Context) -> tuple[int, bool]:
    """아침 첫 실행(06시대)이거나 직전 성공 수집에서 morning_gap_hours 넘게 지났으면(밤사이·실행 누락) 많이 받음."""
    s = ctx.cfg.sources["naver_news"]
    last = (ctx.state.get("last_success") or {}).get(ID)
    gap = (ctx.now - datetime.fromisoformat(last)).total_seconds() / 3600 if last else 999
    morning = ctx.now.hour < 7 or gap >= s.get("morning_gap_hours", 5)
    return (min(100, s.get("per_keyword_morning", 100)) if morning else s["per_keyword"]), morning


def collect(ctx: Context) -> SourceResult:
    s = ctx.cfg.sources["naver_news"]
    if not s.get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    if not has_naver_keys():
        raise SkipSource("네이버 키 없음 — RSS만으로 수집합니다")

    queries = ctx.cfg.naver_queries()
    room = s["daily_call_limit"] - _calls_today(ctx)
    if room <= 0:
        raise SkipSource(f"오늘 호출 상한({s['daily_call_limit']:,}회)에 도달 — 내일 0시에 다시 시작")
    result = SourceResult()
    if room < len(queries):
        result.notes.append(f"오늘 남은 호출 {room}회 → 검색어 {len(queries)}개 중 {room}개만 검색")
        queries = queries[:room]

    per_keyword, morning = per_keyword_now(ctx)
    if morning:
        result.notes.append(f"아침 첫 실행 — 밤사이 기사까지 키워드당 {per_keyword}건")
    headers = {"X-Naver-Client-Id": get_secret("NAVER_CLIENT_ID"), "X-Naver-Client-Secret": get_secret("NAVER_CLIENT_SECRET")}
    fetched_at = ctx.now.isoformat()
    failed = []
    try:
        for field, query in queries:
            try:
                r = ctx.get(URL, params={"query": query, "display": per_keyword, "sort": "date"}, headers=headers)
            except SourceError as e:
                result.calls += 1
                if e.status in (401, 403):
                    raise SourceError(f"네이버 키 인증 실패 — 키를 다시 확인하세요. {e}", e.status) from e
                if e.status == 429:
                    result.notes.append(f"호출이 너무 많아 네이버가 잠시 막음(429) — '{query}'부터 이번 회차는 중단")
                    break
                failed.append(f"{query}({e})")
                continue
            result.calls += 1
            result.articles += parse_items(r.json(), query, field, fetched_at)
            time.sleep(0.05)
    finally:
        _add_calls(ctx, result.calls)

    if failed:
        if len(failed) == result.calls:
            raise SourceError(f"검색 {len(failed)}건 모두 실패. 첫 오류: {failed[0]}")
        result.notes.append(f"검색 {len(failed)}건 실패: " + "; ".join(failed[:3]) + (" …" if len(failed) > 3 else ""))
    return result
