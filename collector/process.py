"""3단계: 모은 기사 → 걸러내기 → 분류 → 같은 사건 묶기 → 연결 → 점수 → 오늘 추천.

혼자 실행(이미 모은 기사로 다시 계산):  python -m collector.process
결과: data/issues.json (화면용 이슈 목록), data/today.json (오늘 쓸 글 추천 3개)
"""
from __future__ import annotations

import sys
import time
from collections import Counter

from . import bridge as bridge_mod
from .classify import RegionTagger, article_text, classify, detect_stage, region_display, region_label
from .cluster import agency_map_from, cluster_articles
from .config import Config
from .filters import ad_words, exclusion_reason, foreign_business, people_words
from .models import Article
from .recommend import pick_today, written_match
from .scoring import score_issue
from .sources import datalab
from .sources.base import Context, SkipSource
from .http import SourceError
from .store import SOURCE_PRIORITY

FIELD_SHARE = 0.3          # 주 분야 점수의 30% 이상인 분야만 함께 표시
MAX_TEXT_MEMBERS = 15      # 연결·단계 판단에 쓰는 기사 수


def _issue_from_cluster(members: list[Article], cls: dict, tagger: RegionTagger, cfg: Config) -> dict:
    total: Counter = Counter()
    hit_count: dict[str, Counter] = {}
    for a in members:
        scores, hits = cls[a.id]
        total.update(scores)
        for f, terms in hits.items():
            hit_count.setdefault(f, Counter()).update(terms)
    top = total.most_common(1)[0][1] if total else 0
    fields = [f for f, s in total.most_common() if s >= top * FIELD_SHARE] or [members[0].field or "etc"]
    primary = fields[0]

    core = [k for k, _ in (hit_count.get(primary) or Counter()).most_common(3)]
    for f in fields[1:]:
        for k, _ in (hit_count.get(f) or Counter()).most_common(1):
            if k not in core:
                core.append(k)
    core = core[:5]

    recent = sorted(members, key=lambda a: a.published, reverse=True)
    rep = min(recent, key=lambda a: SOURCE_PRIORITY.get(a.source, 9))   # 네이버(원문 링크) 우선, 같으면 최신
    latest = recent[0].published
    text = " ".join(article_text(a, cfg) for a in recent[:MAX_TEXT_MEMBERS])

    region_counts: Counter = Counter()
    for a in recent[:MAX_TEXT_MEMBERS]:
        for t in tagger.tag(f"{a.title} {a.summary}", a.region_hint):
            region_counts[(t["sido"], t["area"])] += 1
    tags = [{"sido": s, "area": ar} for (s, ar), _ in region_counts.most_common(3)]

    stage = detect_stage(text, fields, cfg)
    br = bridge_mod.connect(fields, text, stage, tags, region_label(tags), cfg)
    f = cfg.fields.get(primary)
    return {
        "id": rep.id,
        "title": rep.title,
        "url": rep.url,
        "press": rep.press,
        "published": rep.published,
        "latest": latest,
        "summary": next((a.summary for a in recent if a.summary), ""),
        "primary_field": primary,
        "field_name": f.name if f else primary,
        "group": f.group if f else "",
        "tab": f.tab if f else "",
        "fields": fields,
        "core_keywords": core,
        "related_count": len(members),
        "press_count": len({a.press for a in members}),
        "has_briefing": any(a.source == "briefing" for a in members),
        "regions": [region_display(t) for t in tags],
        "region_tags": tags,
        "stage": stage,
        "ad": ad_words(rep.title, cfg),
        "people": people_words(rep.title, cfg),
        "bridge": br,
        "articles": [{"title": a.title, "url": a.url, "press": a.press, "published": a.published, "source": a.source}
                     for a in recent[:6]],
    }


def _cap(issues: list[dict], cfg: Config) -> list[dict]:
    """점수 상위 max_issues 개 + 탭마다 최소 min_per_tab 개."""
    sc = cfg.scoring
    ranked = sorted(issues, key=lambda i: i["scores"]["total"], reverse=True)
    keep = {i["id"] for i in ranked[: sc.get("max_issues", 500)]}
    per_tab: Counter = Counter(i["tab"] for i in ranked if i["id"] in keep)
    for i in ranked:
        if i["id"] not in keep and per_tab[i["tab"]] < sc.get("min_per_tab", 40):
            keep.add(i["id"])
            per_tab[i["tab"]] += 1
    return [i for i in ranked if i["id"] in keep]


def build(articles: list[Article], posts: list[dict], cfg: Config, state: dict, now, get=None) -> dict:
    t0 = time.monotonic()
    excluded: Counter = Counter()
    examples: dict[str, str] = {}
    kept = []
    for a in articles:
        reason = exclusion_reason(a, cfg)
        if reason:
            kind = reason.split("(")[0]
            excluded[kind] += 1
            examples.setdefault(kind, f"{a.press}: {a.title[:40]}")
        else:
            kept.append(a)

    cls = {a.id: classify(a, cfg) for a in kept}
    groups = cluster_articles(kept, cfg.scoring["cluster_similarity"], agency_map_from(cfg.agencies))
    tagger = RegionTagger.build(cfg)
    issues = []
    for m in groups:
        i = _issue_from_cluster(m, cls, tagger, cfg)
        reason = foreign_business(i["title"], i["group"], i["region_tags"], cfg)
        if reason:
            kind = reason.split("(")[0]
            excluded[kind] += len(m)
            examples.setdefault(kind, f"{i['press']}: {i['title'][:40]}")
            continue
        issues.append(i)

    for i in issues:
        i["scores"] = score_issue(i, now, cfg)

    # 데이터랩: 연결이 약하지 않은 상위 이슈의 핵심 키워드만
    ranked = sorted(issues, key=lambda i: i["scores"]["total"], reverse=True)
    targets = [i["core_keywords"] for i in ranked if i["core_keywords"] and i["bridge"]["strength"] != "약함"]
    ctx = Context(cfg, state, now, **({"get": get} if get else {}))
    dl_report = {"id": datalab.ID, "name": datalab.NAME, "status": "ok", "count": 0, "calls": 0, "message": "", "seconds": 0}
    try:
        res = datalab.update(ctx, targets)
        dl_report.update(calls=res["calls"], message=res["message"])
        state.setdefault("last_success", {})[datalab.ID] = now.isoformat()
    except SkipSource as e:
        dl_report.update(status="skipped", message=str(e))
    except SourceError as e:
        dl_report.update(status="failed", message=str(e))
    dl_report["last_success"] = (state.get("last_success") or {}).get(datalab.ID, "")

    days = cfg.scoring["recommend"]["exclude_written_days"]
    for i in issues:
        i["trend"] = datalab.lookup(state, i["core_keywords"]) if i["core_keywords"] else None
        dl_report["count"] += 1 if i["trend"] else 0
        i["scores"] = score_issue(i, now, cfg)
        i["written"] = written_match(i, posts, now, days)

    issues = _cap(issues, cfg)
    today = pick_today(issues, cfg)
    return {
        "issues": issues,
        "today": today,
        "datalab_report": dl_report,
        "stats": {
            "articles_in": len(articles),
            "excluded": dict(excluded),
            "excluded_examples": examples,
            "articles_used": len(kept),
            "issue_groups": len(groups),
            "issues_out": len(issues),
            "seconds": round(time.monotonic() - t0, 1),
        },
    }


def run(cfg: Config, state: dict, now, data_dir, get=None) -> dict:
    """data/articles.json·blog_posts.json 을 읽어 계산하고 issues.json·today.json 을 쓴다."""
    from .store import load_json, save_json
    articles = [Article.from_dict(d) for d in load_json(data_dir / "articles.json", {}).get("articles", [])]
    posts = load_json(data_dir / "blog_posts.json", {}).get("posts", [])
    out = build(articles, posts, cfg, state, now, get=get)
    save_json(data_dir / "issues.json", {"updated_at": now.isoformat(), "issues": out["issues"], "stats": out["stats"]}, compact=True)
    save_json(data_dir / "today.json", {"updated_at": now.isoformat(), "picks": out["today"],
                                       "issues": [i for i in out["issues"] if i["id"] in {p["issue_id"] for p in out["today"]}]},
              compact=True)
    return out


def main() -> int:
    from .config import ConfigError, load_config
    from .env import ROOT_ENV, load_dotenv
    from .models import now_kst
    from .store import DATA_DIR, load_json, save_json
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    load_dotenv(ROOT_ENV)
    try:
        cfg = load_config()
    except ConfigError as e:
        print("❌", e)
        return 1
    now = now_kst()
    state = load_json(DATA_DIR / "state.json", {})
    out = run(cfg, state, now, DATA_DIR)
    save_json(DATA_DIR / "state.json", state)
    print_summary(out)
    return 0


def print_summary(out: dict) -> None:
    s = out["stats"]
    print(f"정제: 기사 {s['articles_in']}건 → 제외 {sum(s['excluded'].values())}건 → 사용 {s['articles_used']}건 "
          f"→ 이슈 {s['issue_groups']}개 → 화면용 {s['issues_out']}개 ({s['seconds']}초)")
    for k, v in s["excluded"].items():
        print(f"   제외 {k}: {v}건 (예: {s['excluded_examples'][k]})")
    d = out["datalab_report"]
    print(f"   검색 추이: {d['status']} — {d['message']}")
    by_id = {i["id"]: i for i in out["issues"]}
    print("오늘 쓸 글 추천:")
    for p in out["today"]:
        i = by_id[p["issue_id"]]
        print(f"   [{p['slot']}] {i['title'][:50]} (관련 {i['related_count']}건, ★{i['scores']['stars']}, "
              f"→ {i['bridge']['categories'][0]}, 총점 {i['scores']['total']})")


if __name__ == "__main__":
    raise SystemExit(main())
