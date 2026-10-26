"""3. Google News RSS 일반 검색 + 지자체 보도자료 (보조 소스).
실행이 2시간 간격(하루 9회)이라 매 실행마다 전체 검색어를 한 바퀴 다 돈다. 검색 사이 1.5초, 막히면 1시간 쉼."""
from __future__ import annotations

from ..http import SourceError
from . import google_rss
from .base import Context, SkipSource, SourceResult

ID = "google_news"
NAME = "구글 뉴스"


def build_queries(cfg) -> list[dict]:
    gn = cfg.sources["google_news"]
    kpq = gn["keywords_per_query"]
    out = []
    for f in cfg.fields.values():
        terms = [k.query for k in f.keywords]
        for i in range(0, len(terms), kpq):
            out.append({"q": f"{google_rss.or_query(terms[i:i + kpq])} when:{gn['window']}", "field": f.id, "source": "google"})
    local = gn.get("local_gov") or {}
    topic = google_rss.or_query(local.get("topic") or ["보도자료"])
    for site in local.get("sites") or []:
        out.append({"q": f"site:{site['site']} {topic} when:{gn['window']}", "field": None, "source": "local_gov",
                    "region_hint": sido_of(cfg, site["name"]), "press": site["name"]})
    return out


def sido_of(cfg, name: str) -> str:
    """'서울시' → '서울', '충청남도' → '충남' (regions.yaml 의 names 로 찾음)"""
    for sido, r in cfg.regions["regions"].items():
        if name in r["names"] or name.startswith(sido):
            return sido
    return ""


def collect(ctx: Context) -> SourceResult:
    if not ctx.cfg.sources["google_news"].get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    google_rss.check_not_blocked(ctx)
    queries = build_queries(ctx.cfg)
    ctx.state.pop("google_round", None)          # 예전(30분 순환) 기록 정리
    result = SourceResult()
    failed = []
    for q in queries:
        try:
            text = google_rss.fetch(ctx, q["q"])
        except SourceError as e:
            result.calls += 1
            if e.status in google_rss.BLOCK_STATUSES:
                until = google_rss.mark_blocked(ctx)
                result.notes.append(f"구글이 요청을 막음(HTTP {e.status}) — {until} 까지 쉬고 다시 시도")
                break
            failed.append(f"{q['q'][:30]}…({e})")
            continue
        result.calls += 1
        arts = google_rss.parse_rss(text, source=q["source"], query=q["q"], field=q["field"],
                                    fetched_at=ctx.now.isoformat(), region_hint=q.get("region_hint", ""))
        if q["source"] == "local_gov":
            for a in arts:
                a.press = q["press"]
        result.articles += arts
    if failed and len(failed) == result.calls:
        raise SourceError(f"검색 {len(failed)}건 모두 실패. 첫 오류: {failed[0]}")
    if failed:
        result.notes.append(f"검색 {len(failed)}건 실패: " + "; ".join(failed[:2]))
    return result
