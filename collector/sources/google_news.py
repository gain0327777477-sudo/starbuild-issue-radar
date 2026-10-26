"""3. Google News RSS 일반 검색 + 지자체 보도자료 (보조 소스).
막힐 위험을 줄이려고 전체 검색어를 4묶음으로 나눠 30분마다 한 묶음씩 → 같은 검색어는 2시간에 한 번."""
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


def this_round(cfg, state: dict) -> tuple[list[dict], int, int]:
    """이번 회차에 할 검색어 묶음. (검색어들, 몇 번째 묶음, 전체 묶음 수)"""
    gn = cfg.sources["google_news"]
    rounds = max(1, round(gn["interval_hours"] * 60 / cfg.sources["collect_every_minutes"]))
    idx = state.get("google_round", 0) % rounds
    return build_queries(cfg)[idx::rounds], idx, rounds


def collect(ctx: Context) -> SourceResult:
    if not ctx.cfg.sources["google_news"].get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    google_rss.check_not_blocked(ctx)
    queries, idx, rounds = this_round(ctx.cfg, ctx.state)
    ctx.state["google_round"] = idx + 1
    result = SourceResult()
    result.notes.append(f"검색어 {rounds}묶음 중 {idx + 1}번째 묶음 ({len(queries)}건)")
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
