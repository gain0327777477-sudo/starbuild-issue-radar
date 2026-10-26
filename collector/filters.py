"""해외 기사·기사 아닌 페이지 제외, 광고성 기사 감점 (설정: config/filters.yaml)."""
from __future__ import annotations

from .config import Config
from .models import Article
from .text import HANGUL


def exclusion_reason(a: Article, cfg: Config) -> str | None:
    """제외해야 하면 이유(화면·로그용), 아니면 None."""
    f = cfg.filters
    title = a.title
    if f.get("require_hangul_title", True) and not HANGUL.search(title):
        return "해외 기사(제목에 한글 없음)"

    domain = (a.domain or "").lower()
    for d in f.get("foreign_domains") or []:
        d = d.lower()
        if domain == d or domain.endswith("." + d) or (d in a.url.lower() and a.source == "naver"):
            return f"해외 매체({d})"
    for tld in f.get("foreign_tlds") or []:
        if domain.endswith(tld.lower()):
            return f"해외 매체({domain})"
    press = a.press.lower()
    for s in f.get("foreign_sources") or []:
        if s.lower() == press or s.lower() in press:
            return f"해외 매체({a.press})"

    anchors = (f.get("domestic_anchors") or []) + region_names(cfg)   # '충남'·'부산' 같은 국내 지역도 국내 연관 단어
    for w in f.get("foreign_words") or []:
        if w in title and not any(x in title for x in anchors):
            return f"해외 지명·지수({w})"

    junk = list(f.get("junk_titles") or [])
    if a.source == "local_gov":
        junk += f.get("junk_titles_local_gov") or []
    for w in junk:
        if w in title:
            return f"기사 아닌 페이지({w.strip()})"
    return None


def region_names(cfg: Config) -> list[str]:
    names = getattr(cfg, "_region_names", None)
    if names is None:
        names = sorted({n for r in cfg.regions["regions"].values() for n in r["names"]} | set(cfg.regions["regions"]))
        cfg._region_names = names
    return names


def foreign_business(title: str, group: str, region_tags: list[dict], cfg: Config) -> str | None:
    """사업형 이슈인데 해외 지명만 있고 국내 연결고리(지역 태그·국내 연관 단어)가 없으면 제외 이유."""
    if group != "사업형" or region_tags:
        return None
    keep = (cfg.filters.get("domestic_anchors") or []) + (cfg.filters.get("foreign_business_keep") or []) + region_names(cfg)
    if any(x in title for x in keep):
        return None
    for w in cfg.filters.get("foreign_words_business") or []:
        if w in title:
            return f"해외 개발·사업 소식({w})"
    return None


def people_words(title: str, cfg: Config) -> list[str]:
    """인물·인사 기사로 볼 낱말. 비어 있으면 인물 기사가 아님(또는 정책 기사로 인정).
    - 별세·부고·동정(people_words_strict): 항상 인물 기사
    - 임명·취임 등(people_words): 제목에 정책 단어(대책·금리·규제·공급·세제·대출)가 있으면 정책 기사로 봄"""
    strict = [w for w in cfg.filters.get("people_words_strict") or [] if w in title]
    if strict:
        return strict
    hits = [w for w in cfg.filters.get("people_words") or [] if w in title]
    if hits and any(w in title for w in cfg.filters.get("people_policy_words") or []):
        return []
    return hits


def ad_words(title: str, cfg: Config) -> tuple[str, list[str]] | None:
    """광고성 단어. ('strong'|'soft', [찾은 단어]) 또는 None."""
    strong = [w for w in cfg.filters.get("ad_words_strong") or [] if w in title]
    if strong:
        return "strong", strong
    soft = [w for w in cfg.filters.get("ad_words_soft") or [] if w in title]
    if soft:
        return "soft", soft
    return None
