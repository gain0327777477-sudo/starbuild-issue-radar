"""오늘 쓸 글 추천 3개 + 내 블로그 최근 글과 겹치는지 확인."""
from __future__ import annotations

from datetime import datetime, timedelta

from .cluster import bigrams, normalize_title, similarity


def written_match(issue: dict, posts: list[dict], now: datetime, days: int) -> dict | None:
    """최근 days 일 안에 쓴 블로그 글 중 이 이슈와 주제가 겹치는 글.
    겹침 = 핵심 키워드 2개 이상이 제목에 있거나, 1개 + 제목이 꽤 비슷함."""
    cutoff = (now - timedelta(days=days)).isoformat()
    core = [k for k in issue.get("core_keywords", []) if len(k) >= 2]
    g_issue = bigrams(normalize_title(issue["title"], {}))
    for p in posts:
        if p["published"] < cutoff:
            continue
        hits = [k for k in core if k.replace(" ", "") in p["title"].replace(" ", "")]
        sim = similarity(g_issue, bigrams(normalize_title(p["title"], {})))
        if len(hits) >= 2 or (hits and sim >= 0.15) or sim >= 0.35:
            return {"title": p["title"], "url": p["url"], "published": p["published"], "keywords": hits}
    return None


def pick_today(issues: list[dict], cfg) -> list[dict]:
    """scoring.yaml 의 recommend.slots 순서대로 1개씩.
    제외: 블로그에 최근 쓴 주제, 연결 약함, 인물·인사 기사(exclude_people).
    rank_by: attract 인 칸(집객형)은 집객 점수 순, 나머지는 총점 순."""
    rec = cfg.scoring["recommend"]
    picked, used = [], set()
    for slot in rec["slots"]:
        key = "attract" if slot.get("rank_by") == "attract" else "total"
        ranked = sorted(issues, key=lambda i: (i["scores"][key], i["scores"]["total"]), reverse=True)
        choice = None
        for group in (slot.get("fields") or [], slot.get("backup_fields") or []):
            for i in ranked:
                if i["id"] in used or i.get("written") or i["bridge"]["strength"] == "약함":
                    continue
                if rec.get("exclude_people") and i.get("people"):
                    continue
                if i["primary_field"] in group:
                    choice = i
                    break
            if choice:
                break
        if choice:
            used.add(choice["id"])
            picked.append({"slot": slot["name"], "issue_id": choice["id"], "ranked_by": "집객 점수" if key == "attract" else "총점",
                           "from_backup": choice["primary_field"] not in (slot.get("fields") or [])})
    return picked
