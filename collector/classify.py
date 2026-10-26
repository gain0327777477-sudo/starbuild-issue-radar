"""기사 → 분야 분류, 지역 태그, 진행 단계."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import Config
from .models import Article
from .text import contains, strip_phrases


def article_text(a: Article, cfg: Config) -> str:
    return strip_phrases(f"{a.title} {a.summary}", cfg.filters.get("classify_ignore") or [])


# ── 분야 ─────────────────────────────────────

def classify(a: Article, cfg: Config) -> tuple[Counter, dict[str, list[str]]]:
    """분야별 점수(제목 2점, 요약 1점)와 걸린 키워드. 아무것도 안 걸리면 검색어의 분야로."""
    ignore = cfg.filters.get("classify_ignore") or []
    title = strip_phrases(a.title, ignore)
    summary = strip_phrases(a.summary, ignore)
    scores: Counter = Counter()
    hits: dict[str, list[str]] = {}
    for f in cfg.fields.values():
        for kw in f.keywords:
            for term in kw.terms:
                w = 2 if contains(title, term) else (1 if contains(summary, term) else 0)
                if w:
                    scores[f.id] += w
                    hits.setdefault(f.id, []).append(kw.query)
                    break
    c = cfg.company_combos
    if c:
        co = next((x.query for x in c["companies"] if any(contains(title, t) for t in x.terms)), None)
        act = next((x.query for x in c["actions"] if any(contains(title, t) for t in x.terms)), None)
        if co and act:
            scores[c["field"]] += 2
            hits.setdefault(c["field"], []).append(f"{co} {act}")
    if not scores and a.field:
        scores[a.field] = 1
    return scores, hits


# ── 지역 ─────────────────────────────────────

@dataclass
class _Entry:
    term: str
    places: list[tuple[str, str]]   # [(시·도, 시·군·구 표시 이름 또는 "")]

    @property
    def ambiguous(self) -> bool:     # 여러 시·도에 같은 이름(중구·고성군 등) → 시·도 이름이 같이 나와야 인정
        return len({s for s, _ in self.places}) > 1 and all(a for _, a in self.places)


@dataclass
class RegionTagger:
    entries: list[_Entry] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    sido_names: dict[str, list[str]] = field(default_factory=dict)
    guarded: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # 짧은 이름 → 시·도와 함께일 때만

    @classmethod
    def build(cls, cfg: Config) -> "RegionTagger":
        reg = cfg.regions
        require_full = set(reg["require_full_name"])
        t = cls(ignore=reg["ignore_phrases"])
        by_term: dict[str, list[tuple[str, str]]] = {}
        for sido, r in reg["regions"].items():
            t.sido_names[sido] = r["names"] + [sido]
            for name in r["names"]:
                by_term.setdefault(name, []).append((sido, ""))
        for alias, sidos in reg["combined_aliases"].items():
            by_term[alias] = [(s, "") for s in sidos]
        sido_name_set = set(by_term)
        for sido, r in reg["regions"].items():
            for terms in r["areas"]:
                display = re.sub(r"\(.*?\)", "", terms[0])
                match_terms = [x for x in terms if "(" not in x]
                stem = re.sub(r"[시군구]$", "", display)
                if len(stem) >= 2 and stem != display:
                    if stem in require_full:
                        t.guarded.setdefault(stem, []).append((sido, display))
                    else:
                        match_terms.append(stem)
                for m in match_terms:
                    if m not in sido_name_set:         # '광주' 같은 시·도 이름은 시·도로만
                        by_term.setdefault(m, []).append((sido, display))
        t.entries = sorted((_Entry(k, v) for k, v in by_term.items()), key=lambda e: len(e.term), reverse=True)
        return t

    def tag(self, text: str, hint_sido: str = "") -> list[dict]:
        """긴 이름부터 찾고, 찾은 자리는 가려서 '경기 광주' 안의 '광주'가 다시 걸리지 않게 한다."""
        text = strip_phrases(text, self.ignore)
        sidos_found: list[str] = [hint_sido] if hint_sido else []
        areas: list[tuple[str, str]] = []
        pending: list[_Entry] = []
        masked = text
        for e in self.entries:
            if e.term not in masked:
                continue
            masked = masked.replace(e.term, "□" * len(e.term))
            if e.ambiguous:
                pending.append(e)
                continue
            for sido, area in e.places:
                if area:
                    areas.append((sido, area))
                else:
                    sidos_found.append(sido)
        for stem, places in self.guarded.items():
            for sido, display in places:
                if any(f"{n} {stem}" in text for n in self.sido_names[sido]):
                    areas.append((sido, display))
        found_sidos = set(sidos_found) | {s for s, _ in areas}
        for e in pending:
            areas += [(s, a) for s, a in e.places if s in found_sidos]
        out, seen = [], set()
        for sido, area in areas:
            if (sido, area) not in seen:
                seen.add((sido, area))
                out.append({"sido": sido, "area": area})
        for sido in sidos_found:
            if sido not in {s for s, _ in areas} and (sido, "") not in seen:
                seen.add((sido, ""))
                out.append({"sido": sido, "area": ""})
        return out


def region_label(tags: list[dict]) -> str:
    """연결 문장용 짧은 지역 이름: '포항', '서울'. 없으면 ''."""
    if not tags:
        return ""
    first = next((t for t in tags if t["area"]), tags[0])
    if first["area"]:
        return re.sub(r"(?<=..)[시군]$", "", first["area"])
    return first["sido"]


def region_display(tag: dict) -> str:
    return f"{tag['sido']} {tag['area']}".strip()


# ── 진행 단계 ─────────────────────────────────

def detect_stage(text: str, fields: list[str], cfg: Config) -> dict | None:
    st = cfg.stages
    group = None
    for gname in ("transport", "development"):        # 교통이 주 분야면 교통 단계를 씀
        if fields and fields[0] in st[gname]["fields"]:
            group = gname
            break
    if group is None:
        for gname in ("transport", "development"):
            if any(f in st[gname]["fields"] for f in fields):
                group = gname
                break
    if group is None:
        return None
    stages = st[group]["stages"]

    # 단계 낱말을 긴 것부터 찾고, 찾은 자리는 가림 → '유치 희망'(건의·요구) 안의 '유치'(계획)가 다시 걸리지 않게
    counts = {s["id"]: [0, 0] for s in stages}       # [실제로 일어난 언급 수, 예정·희망 언급 수]
    pairs = sorted(((w, s["id"]) for s in stages for w in s["words"]), key=lambda x: len(x[0]), reverse=True)
    masked = text
    for w, sid in pairs:
        for m in re.finditer(re.escape(w), masked):
            after = text[m.end(): m.end() + 8]
            before = text[max(0, m.start() - 4): m.start()]
            before_long = text[max(0, m.start() - 12): m.start()]
            if any(x in after[:7] for x in st["ignore_after_words"]) or                     any(re.search(p, after) for p in st["ignore_after_patterns"]):
                continue                             # '준공 50주년'·'개통 50년 만에' 같은 옛날 일
            future = any(x in after for x in st["future_words"]) or any(x in before for x in st["future_prefix_words"])                 or any(re.search(p, before_long) for p in st["future_prefix_patterns"])     # '12월 개통'·'내년 착공'

            counts[sid][1 if future else 0] += 1
        masked = masked.replace(w, "□" * len(w))
    # 건의·요구 표현이 있으면 '구축계획' 같은 문서 이름은 계획 단계로 세지 않음
    lowest = stages[0]["id"]
    if sum(counts[lowest]) and len(stages) > 1:
        docs = sum(text.count(d) for d in st["document_words"])
        plan_id = stages[1]["id"]
        counts[plan_id][0] = max(0, counts[plan_id][0] - docs)

    # 더 진행된 단계부터 내려가며: 실제로 일어났다는 언급이 있는 가장 높은 단계 = 현재 단계
    # 그보다 높은 단계가 '예정'으로만 나오면 next 로 기록 (예: 착공했고 2028년 개통 예정)
    current, upcoming_stage = None, None
    for s in reversed(stages):
        actual, future = counts[s["id"]]
        if actual and current is None and (actual >= future or s is stages[0]):
            current = s
            break
        if future and upcoming_stage is None:
            upcoming_stage = s
    matched = current is not None or upcoming_stage is not None      # 단계 낱말을 하나라도 찾았는지
    upcoming = False
    if current is None and upcoming_stage is not None:
        current, upcoming = upcoming_stage, upcoming_stage is not stages[0]   # 계획 단계는 원래 '앞으로의 일'
    if current is None:
        current = next(s for s in stages if s["id"] == st["default_stage"])
    nxt = upcoming_stage if (upcoming_stage is not None and upcoming_stage is not current) else None

    delayed = any(w in text for w in st["delay_words"])
    notice = current["notice"] if current.get("cautious") else (st["upcoming_notice"] if upcoming else "")
    if nxt is not None and not notice:
        notice = st["upcoming_notice"]
    return {
        "group": group,
        "id": current["id"],
        "name": current["name"] + (" 예정" if upcoming else ""),
        "next": f"{nxt['name']} 예정" if nxt is not None else "",
        "points": current["points"] / 2 if upcoming else current["points"],
        "cautious": bool(current.get("cautious")) or upcoming,
        "upcoming": upcoming,
        "delayed": delayed,
        "notice": notice,
        "matched": matched,
    }
