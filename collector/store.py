"""data/*.json 읽기·쓰기와 7일 보관."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import Article

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # 깨진 파일은 옆에 남겨두고 새로 시작 (수집이 멈추지 않도록)
        path.replace(path.with_suffix(path.suffix + ".broken"))
        return default


def save_json(path: Path, data, compact: bool = False) -> None:
    """반쯤 쓰다 끊겨도 파일이 깨지지 않게 임시 파일에 쓴 뒤 바꿔치기.
    compact=True: 핸드폰이 받는 파일은 공백 없이 작게."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(data, ensure_ascii=False, indent=1)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


ARTICLE_DIR = "articles"          # data/articles/YYYY-MM-DD.json — 발행일별로 나눠 저장


def load_articles(data_dir: Path) -> list[Article]:
    """보관 중인 기사 전부. 날짜별 파일을 모두 읽고, 예전 한 파일(articles.json)이 남아 있으면 그것도 읽음."""
    out: list[Article] = []
    for f in sorted((data_dir / ARTICLE_DIR).glob("*.json")):
        out += [Article.from_dict(d) for d in load_json(f, {}).get("articles", [])]
    legacy = data_dir / "articles.json"
    if legacy.exists():
        out += [Article.from_dict(d) for d in load_json(legacy, {}).get("articles", [])]
    return out


def save_articles(data_dir: Path, articles: list[Article], now: datetime) -> dict[str, int]:
    """발행일별 파일로 공백 없이 저장 (GitHub 는 파일 하나가 100MB 를 넘으면 거부하므로 나눔).
    보관 기간이 지나 기사가 없는 날짜 파일과 예전 한 파일(articles.json)은 지움. 반환: {파일 이름: 바이트}"""
    folder = data_dir / ARTICLE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for a in articles:
        by_day.setdefault(a.published[:10], []).append(a.to_dict())
    sizes = {}
    for day, items in by_day.items():
        path = folder / f"{day}.json"
        save_json(path, {"updated_at": now.isoformat(), "articles": items}, compact=True)
        sizes[path.name] = path.stat().st_size
    for f in folder.glob("*.json"):
        if f.name not in sizes:
            f.unlink()
    legacy = data_dir / "articles.json"
    if legacy.exists():
        legacy.unlink()
    return sizes


def title_key(title: str) -> str:
    """같은 기사 판별용: 띄어쓰기·기호를 뺀 제목."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title).lower()


SOURCE_PRIORITY = {"naver": 0, "briefing": 1, "local_gov": 2, "google": 3}   # 겹치면 앞쪽을 남김 (네이버 = 원문 링크·요약 있음)


def merge_articles(old: list[Article], new: list[Article], now: datetime, retention_days: int) -> list[Article]:
    """이전 기사 + 새 기사 → 중복 제거, 보관 기간 지난 것 삭제, 최신순."""
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    by_key: dict[str, Article] = {}
    for a in old + new:
        if a.published < cutoff:
            continue
        key = title_key(a.title)
        if not key:
            continue
        cur = by_key.get(key)
        if cur is None or SOURCE_PRIORITY.get(a.source, 9) < SOURCE_PRIORITY.get(cur.source, 9):
            if cur is not None:
                a.agency = a.agency or cur.agency
                a.region_hint = a.region_hint or cur.region_hint
            by_key[key] = a
    seen_urls = set()
    out = []
    for a in sorted(by_key.values(), key=lambda x: x.published, reverse=True):
        if a.url in seen_urls:
            continue
        seen_urls.add(a.url)
        out.append(a)
    return out
