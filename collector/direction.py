"""방향 판정 — 환율·금리·증시·예금금리 기사가 오르는 쪽인지 내리는 쪽인지 (설정: config/directions.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache

DIRECTIONS = ("up", "down", "hold")


@lru_cache(maxsize=2048)
def _pattern(phrase: str) -> re.Pattern:
    """'실효환율~최고' → 사이에 12자까지 허용"""
    parts = [re.escape(p) for p in phrase.split("~")]
    return re.compile(".{0,12}?".join(parts))


def count_hits(text: str, topic: dict) -> tuple[dict[str, int], list[str]]:
    """긴 표현(phrases)을 먼저 찾고 그 자리를 지운 뒤 짧은 낱말(words)을 센다."""
    counts = {d: 0 for d in DIRECTIONS}
    found = []
    items = [(p, d) for d in DIRECTIONS for p in (topic.get("phrases") or {}).get(d, [])]
    items.sort(key=lambda x: len(x[0]), reverse=True)
    for phrase, d in items:
        pat = _pattern(phrase)
        for m in list(pat.finditer(text)):
            counts[d] += 1
            found.append(m.group(0))
        text = pat.sub(lambda m: "□" * len(m.group(0)), text)
    for d in DIRECTIONS:
        for w in (topic.get("words") or {}).get(d, []):
            n = text.count(w)
            if n:
                counts[d] += n
                found.append(w)
    return counts, found


def detect(topic_id: str, rep_title: str, other_titles: list[str], cfg) -> dict:
    conf = cfg.directions
    topic = conf["topics"][topic_id]
    total = {d: 0 for d in DIRECTIONS}
    words = []
    for title, weight in [(rep_title, conf.get("title_weight", 3))] + [(t, 1) for t in other_titles[: conf.get("other_titles", 5)]]:
        c, f = count_hits(title, topic)
        for d in DIRECTIONS:
            total[d] += c[d] * weight
        if weight > 1:
            words += f
    dom = conf.get("dominance", 1.5)
    up, down, hold = total["up"], total["down"], total["hold"]
    if hold and hold >= max(up, down):
        result = "hold"
    elif up and up >= down * dom:
        result = "up"
    elif down and down >= up * dom:
        result = "down"
    else:
        result = "unclear"
    return {
        "topic": topic_id,
        "id": result,
        "label": topic["labels"].get(result, topic["labels"].get("unclear", "방향 불분명")),
        "counts": total,
        "words": sorted(set(words)),
    }
