"""2. 정책브리핑 보도자료 — Google News 'site:korea.kr 부처명' 검색 (30분마다).
korea.kr RSS는 2026-07-01 서비스 중단되어 이 방법을 쓴다."""
from __future__ import annotations

from ..http import SourceError
from . import google_rss
from .base import Context, SkipSource, SourceResult

ID = "korea_briefing"
NAME = "정책브리핑"


def build_queries(cfg) -> list[tuple[str, str]]:
    """[(부처명, 'site:korea.kr (재정경제부 OR 기획재정부 OR …) when:3d'), …]"""
    s = cfg.sources["korea_briefing"]
    return [(d, f"site:korea.kr {google_rss.or_query(cfg.agency_terms(d))} when:{s['window']}") for d in s["departments"]]


def collect(ctx: Context) -> SourceResult:
    if not ctx.cfg.sources["korea_briefing"].get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    google_rss.check_not_blocked(ctx)
    result = SourceResult()
    failed = []
    for dept, q in build_queries(ctx.cfg):
        try:
            text = google_rss.fetch(ctx, q)
        except SourceError as e:
            result.calls += 1
            if e.status in google_rss.BLOCK_STATUSES:
                until = google_rss.mark_blocked(ctx)
                result.notes.append(f"구글이 요청을 막음(HTTP {e.status}) — {until} 까지 쉬고 다시 시도")
                break
            failed.append(f"{dept}({e})")
            continue
        result.calls += 1
        result.articles += google_rss.parse_rss(text, source="briefing", query=q, field=None,
                                                fetched_at=ctx.now.isoformat(), agency=dept)
    if failed and len(failed) == result.calls:
        raise SourceError(f"부처 {len(failed)}곳 모두 실패. 첫 오류: {failed[0]}")
    if failed:
        result.notes.append("일부 부처 실패: " + "; ".join(failed[:3]))
    if not result.articles and result.notes:
        raise SourceError(result.notes[0])
    return result
