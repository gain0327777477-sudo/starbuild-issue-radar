"""같은 사건 기사 묶기 — 제목 유사도(글자 2개씩 겹치는 비율)로 한 이슈로 묶는다."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from .models import Article

TAGS = re.compile(r"\[[^\]]{1,10}\]|【[^】]{1,10}】|\((종합|속보|단독|1보|2보|상보|사진|영상)\)|^(속보|단독)\s")
MAX_GAP = timedelta(hours=48)   # 이틀 넘게 떨어진 기사는 같은 사건으로 보지 않음


def normalize_title(title: str, agency_map: dict[str, str]) -> str:
    t = TAGS.sub(" ", title)
    for old, new in agency_map.items():     # '기재부' → '재정경제부' 처럼 기관 이름을 통일
        t = t.replace(old, new)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()


def bigrams(s: str) -> frozenset[str]:
    return frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset([s])


def similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def agency_map_from(agencies: list[list[str]]) -> dict[str, str]:
    m = {}
    for terms in agencies:
        for old in terms[1:]:
            m.setdefault(old, terms[0])
    return dict(sorted(m.items(), key=lambda kv: len(kv[0]), reverse=True))


def cluster_articles(articles: list[Article], threshold: float, agency_map: dict[str, str] | None = None) -> list[list[Article]]:
    """오래된 기사부터 하나씩: 비슷한 묶음이 있으면 들어가고, 없으면 새 묶음."""
    agency_map = agency_map or {}
    clusters: list[dict] = []                    # {"members": [...], "grams": [set,...], "last": datetime}
    index: dict[str, set[int]] = defaultdict(set)
    for a in sorted(articles, key=lambda x: x.published):
        g = bigrams(normalize_title(a.title, agency_map))
        when = datetime.fromisoformat(a.published)
        counts: dict[int, int] = defaultdict(int)
        for gram in g:
            for cid in index.get(gram, ()):
                counts[cid] += 1
        best, best_sim = None, 0.0
        for cid, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
            c = clusters[cid]
            if when - c["last"] > MAX_GAP:
                continue
            sim = max(similarity(g, other) for other in c["grams"])
            if sim > best_sim:
                best, best_sim = cid, sim
        if best is not None and best_sim >= threshold:
            c = clusters[best]
            c["members"].append(a)
            c["last"] = max(c["last"], when)
            if len(c["grams"]) < 6:              # 대표 제목 몇 개만 비교 대상으로
                c["grams"].append(g)
                for gram in g:
                    index[gram].add(best)
        else:
            clusters.append({"members": [a], "grams": [g], "last": when})
            for gram in g:
                index[gram].add(len(clusters) - 1)
    return [c["members"] for c in clusters]
