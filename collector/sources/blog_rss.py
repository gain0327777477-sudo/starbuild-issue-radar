"""5. 내 블로그 RSS — 최근 발행 글 제목 (이미 쓴 주제 중복 경고용)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..http import SourceError
from ..models import clean_text, parse_rfc822
from .base import Context, SkipSource, SourceResult

ID = "blog_rss"
NAME = "내 블로그"


def parse_posts(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"블로그 RSS 형식이 아님: {e}; 응답 앞부분: {xml_text[:120]}")
    posts = []
    for item in root.iter("item"):
        published = parse_rfc822(item.findtext("pubDate") or "")
        title = clean_text(item.findtext("title"))
        if title and published:
            posts.append({
                "title": title,
                "url": (item.findtext("link") or "").strip().split("?")[0],
                "published": published.isoformat(),
                "category": clean_text(item.findtext("category")),
            })
    return posts


def collect(ctx: Context) -> SourceResult:
    if not ctx.cfg.sources["blog_rss"].get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    url = ctx.cfg.blog_rss_url
    if not url:
        raise SkipSource("office.yaml 에 블로그 주소가 없음")
    r = ctx.get(url)
    posts = parse_posts(r.content.decode("utf-8", errors="replace"))
    if not posts:
        raise SourceError(f"블로그 RSS 에 글이 0개입니다 — 주소 확인 필요: {url}")
    return SourceResult(calls=1, extra={"posts": posts})
