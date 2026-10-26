"""도메인 → 언론사 이름. 네이버 뉴스 API는 언론사 이름을 주지 않아서 원문 주소로 알아낸다.

우선순위: config/press.yaml(대표님이 적은 이름) > 구글 뉴스에서 자동으로 배운 이름(state.json) > 도메인 그대로
"""
from __future__ import annotations

import re
from collections import Counter

DOMAIN_LIKE = re.compile(r"[a-z0-9.-]+\.[a-z]{2,}")
STOP = {"co.kr", "or.kr", "go.kr", "ne.kr", "com", "kr", "net", "org"}


def candidates(domain: str):
    """news.hankyung.com → news.hankyung.com, hankyung.com"""
    d = domain.lower()
    while d and d not in STOP:
        yield d
        if "." not in d:
            break
        d = d.split(".", 1)[1]


def press_name(domain: str, config_map: dict | None = None, learned: dict | None = None) -> str:
    for mapping in (config_map or {}, learned or {}):
        for d in candidates(domain):
            if d in mapping:
                return mapping[d]
    return domain


def looks_like_domain(press: str) -> bool:
    return bool(DOMAIN_LIKE.fullmatch(press or ""))


def learn(articles, learned: dict) -> int:
    """구글 뉴스 기사(언론사 이름 + 언론사 주소)에서 '도메인 → 이름'을 배움. 새로 배운 개수."""
    seen: dict[str, Counter] = {}
    for a in articles:
        if a.source == "google" and a.domain and a.press and not looks_like_domain(a.press) and a.press != "Google News":
            seen.setdefault(a.domain, Counter())[a.press] += 1
    new = 0
    for d, c in seen.items():
        name = c.most_common(1)[0][0]
        if d not in learned:
            new += 1
        learned[d] = name
    return new


def fix_press(articles, config_map: dict, learned: dict) -> int:
    """언론사 칸이 도메인으로 남아 있거나 설정으로 바뀐 이름이 있으면 고침. 고친 개수."""
    fixed = 0
    for a in articles:
        if a.source not in ("naver",) and not looks_like_domain(a.press):
            continue
        name = press_name(a.domain, config_map, learned)
        if name != a.press:
            a.press = name
            fixed += 1
    return fixed
