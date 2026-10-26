"""집객 점수 + 사업 연결 점수 (설정: config/scoring.yaml). 모든 점수에 근거를 남긴다."""
from __future__ import annotations

from datetime import datetime


def _r(part: str, label: str, points: float) -> dict:
    return {"part": part, "label": label, "points": round(points, 1)}


def score_issue(issue: dict, now: datetime, cfg) -> dict:
    """issue 에 필요한 값: related_count, latest, primary_field, has_briefing, stage, trend, ad, bridge"""
    a = cfg.scoring["attract"]
    b = cfg.scoring["business"]
    reasons = []

    n = issue["related_count"]
    reasons.append(_r("집객", f"관련 기사 {n}건", min(n * a["per_related_article"], a["related_article_max"])))

    hours = (now - datetime.fromisoformat(issue["latest"])).total_seconds() / 3600
    for step in a["freshness"]:
        if hours <= step["within_hours"]:
            reasons.append(_r("집객", f"최신성(가장 최근 기사 {max(hours, 0):.0f}시간 전)", step["points"]))
            break

    trend = issue.get("trend")
    if trend and trend.get("rising"):
        reasons.append(_r("집객", "검색 추이 상승(데이터랩 상대 추이)", a["trend_rising"]))

    if issue["primary_field"] in a["public_interest_fields"]:
        reasons.append(_r("집객", "대중 관심 분야", a["public_interest_bonus"]))

    if issue.get("has_briefing"):
        reasons.append(_r("집객", "정부 보도자료 포함", a["briefing_bonus"]))

    stage = issue.get("stage")
    if stage and stage["points"]:
        reasons.append(_r("집객", f"진행 단계: {stage['name']}", stage["points"]))

    ad = issue.get("ad")
    if ad:
        kind, words = ad
        pts = a["ad_penalty_strong"] if kind == "strong" else a["ad_penalty_soft"]
        reasons.append(_r("집객", f"광고성 표현({', '.join(words)})", pts))

    people = issue.get("people")
    if people:
        reasons.append(_r("집객", f"인물·인사 기사({', '.join(people)})", a.get("people_penalty", 0)))

    attract = sum(x["points"] for x in reasons if x["part"] == "집객")

    br = issue["bridge"]
    reasons.append(_r("연결", f"연결 {br['strength']}" + (f" → {br['categories'][0]}" if br["strength"] != "약함" else ""),
                      b[br["strength"]]))
    if br.get("tourist_bonus"):
        reasons.append(_r("연결", "관광지 지역", br["tourist_bonus"]))
    if br["strength"] == "약함":
        reasons.append(_r("연결", "연결 약함 → 추천 순위 내림", b["weak_rank_penalty"]))
    business = sum(x["points"] for x in reasons if x["part"] == "연결")

    stars = 3 if attract >= a["stars"]["three"] else 2 if attract >= a["stars"]["two"] else 1
    return {
        "attract": round(attract, 1),
        "business": round(business, 1),
        "total": round(attract + business, 1),
        "stars": stars,
        "reasons": reasons,
    }
