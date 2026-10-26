"""config/*.yaml 읽기와 검사.

대표님이 설정 파일을 고치다 실수해도 원인을 바로 알 수 있도록,
문제가 있으면 '어느 파일의 어느 항목이 왜 틀렸는지'를 한국어로 모두 모아서 알려준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

FILES = ("office", "blog_categories", "keywords", "bridges", "sources", "filters", "scoring")
GROUPS = ("집객형", "사업형")
TABS = ("증시·경제", "금리·대출", "숙박·관광", "부동산·상권", "지역개발")
STRENGTHS = ("직접", "간접")


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("설정 파일에 문제가 있습니다:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass
class Keyword:
    query: str                 # 네이버·구글 검색에 쓰는 단어
    terms: list[str]           # 기사 분류에 쓰는 단어 전체(query 포함)


@dataclass
class Field:
    id: str
    name: str
    group: str
    tab: str
    keywords: list[Keyword]


@dataclass
class Config:
    office: dict
    categories: list[dict]
    fields: dict[str, Field]
    rules: list[dict]
    cta_templates: dict[str, str]
    office_footer: str
    sources: dict
    filters: dict
    scoring: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def category_names(self) -> list[str]:
        return [c["name"] for c in self.categories]

    def all_keywords(self) -> list[tuple[str, Keyword]]:
        return [(f.id, k) for f in self.fields.values() for k in f.keywords]


def parse_keyword(raw) -> Keyword:
    terms = [t.strip() for t in str(raw).split("|") if t.strip()]
    return Keyword(query=terms[0], terms=terms)


def _read_yaml(path: Path, problems: list[str]) -> dict:
    if not path.exists():
        problems.append(f"{path.name} 파일이 없습니다")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" ({mark.line + 1}번째 줄 근처)" if mark else ""
        problems.append(f"{path.name}{where}: 형식 오류 — 들여쓰기(띄어쓰기 칸 수)나 콜론(:)을 확인하세요. 원문: {e}")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        problems.append(f"{path.name}: 파일 내용이 '항목: 값' 형태가 아닙니다")
        return {}
    return data


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    problems: list[str] = []
    warnings: list[str] = []
    raw = {name: _read_yaml(config_dir / f"{name}.yaml", problems) for name in FILES}

    # office.yaml — 비어 있어도 동작은 하되 경고
    office = {k: ("" if v is None else str(v)) for k, v in raw["office"].items()}
    blank = [k for k in ("name", "phone") if not office.get(k)]
    if blank:
        warnings.append(f"office.yaml: {', '.join(blank)} 이(가) 비어 있습니다 — 상담 안내 문구에서 빠집니다")

    # blog_categories.yaml
    categories = raw["blog_categories"].get("categories") or []
    names = []
    for i, c in enumerate(categories, 1):
        if not isinstance(c, dict) or not c.get("name"):
            problems.append(f"blog_categories.yaml: {i}번째 카테고리에 name 이 없습니다")
            continue
        c.setdefault("url", "")
        c["url"] = c["url"] or ""
        names.append(c["name"])
    if len(names) != len(set(names)):
        problems.append("blog_categories.yaml: 같은 카테고리 이름이 두 번 있습니다")
    empty_urls = [c["name"] for c in categories if isinstance(c, dict) and c.get("name") and not c["url"]]
    if empty_urls:
        warnings.append(f"blog_categories.yaml: URL 미입력 {len(empty_urls)}개 (나중에 입력해도 됩니다)")

    # keywords.yaml
    fields: dict[str, Field] = {}
    for fid, f in (raw["keywords"].get("fields") or {}).items():
        where = f"keywords.yaml의 {fid}"
        if not isinstance(f, dict):
            problems.append(f"{where}: 내용이 비어 있습니다")
            continue
        if f.get("group") not in GROUPS:
            problems.append(f"{where}: group 은 {' / '.join(GROUPS)} 중 하나여야 합니다 (지금: {f.get('group')})")
        if f.get("tab") not in TABS:
            problems.append(f"{where}: tab 은 {' / '.join(TABS)} 중 하나여야 합니다 (지금: {f.get('tab')})")
        kws = [parse_keyword(k) for k in (f.get("keywords") or []) if str(k).strip()]
        if not kws:
            problems.append(f"{where}: keywords 가 비어 있습니다")
        fields[fid] = Field(id=fid, name=str(f.get("name") or fid), group=f.get("group"), tab=f.get("tab"), keywords=kws)
    if not fields:
        problems.append("keywords.yaml: fields 가 비어 있습니다")
    seen: dict[str, str] = {}
    for fid, kw in [(f.id, k) for f in fields.values() for k in f.keywords]:
        if kw.query in seen and seen[kw.query] != fid:
            warnings.append(f"keywords.yaml: '{kw.query}' 가 {seen[kw.query]}, {fid} 두 분야에 있습니다 (검색이 두 번 됩니다)")
        seen.setdefault(kw.query, fid)

    # bridges.yaml
    bridges = raw["bridges"]
    rules = bridges.get("rules") or []
    templates = bridges.get("cta_templates") or {}
    rule_ids = set()
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict):
            problems.append(f"bridges.yaml: {i}번째 규칙 형식이 잘못됐습니다")
            continue
        where = f"bridges.yaml의 규칙 {r.get('id') or i}"
        if r.get("id") in rule_ids:
            problems.append(f"{where}: id 가 중복됩니다")
        rule_ids.add(r.get("id"))
        r["words"] = [str(w) for w in (r.get("words") or [])]
        for fid in r.get("fields") or []:
            if fid not in fields:
                problems.append(f"{where}: fields 의 '{fid}' 는 keywords.yaml 에 없는 분야입니다")
        if not r.get("fields"):
            problems.append(f"{where}: fields 가 비어 있습니다")
        for cat in r.get("categories") or []:
            if cat not in names:
                problems.append(f"{where}: 카테고리 '{cat}' 는 blog_categories.yaml 에 없습니다 (이름을 똑같이 써야 합니다)")
        if not r.get("categories"):
            problems.append(f"{where}: categories 가 비어 있습니다")
        if r.get("strength") not in STRENGTHS:
            problems.append(f"{where}: strength 는 직접 / 간접 중 하나여야 합니다 (지금: {r.get('strength')})")
        if r.get("cta") not in templates:
            problems.append(f"{where}: cta '{r.get('cta')}' 에 해당하는 문구가 cta_templates 에 없습니다")
        for key in ("logic", "sentence"):
            if not r.get(key):
                problems.append(f"{where}: {key} 가 비어 있습니다")

    # sources.yaml
    sources = raw["sources"]
    if not isinstance(sources.get("collect_every_minutes"), int) or sources["collect_every_minutes"] <= 0:
        problems.append("sources.yaml: collect_every_minutes 는 1 이상의 숫자여야 합니다")
    dl = sources.get("datalab") or {}
    if dl.get("groups_per_call", 5) > 5:
        problems.append("sources.yaml: datalab.groups_per_call 은 최대 5입니다 (네이버 규칙)")
    if dl.get("daily_call_limit", 0) > 1000:
        problems.append("sources.yaml: datalab.daily_call_limit 은 네이버 한도 1,000 이하로 두세요")
    if (sources.get("naver_news") or {}).get("daily_call_limit", 0) > 25000:
        problems.append("sources.yaml: naver_news.daily_call_limit 은 네이버 한도 25,000 이하로 두세요")

    # filters.yaml — 목록 항목만 정리
    filters = {k: [str(x) for x in (v or [])] for k, v in raw["filters"].items()}

    # scoring.yaml
    scoring = raw["scoring"]
    for fid in (scoring.get("attract") or {}).get("public_interest_fields") or []:
        if fid not in fields:
            problems.append(f"scoring.yaml: public_interest_fields 의 '{fid}' 는 없는 분야입니다")
    slots = (scoring.get("recommend") or {}).get("slots") or []
    if not slots:
        problems.append("scoring.yaml: recommend.slots 가 비어 있습니다")
    for s in slots:
        for fid in (s.get("fields") or []) + (s.get("backup_fields") or []):
            if fid not in fields:
                problems.append(f"scoring.yaml: 추천 '{s.get('name')}' 의 '{fid}' 는 없는 분야입니다")

    if problems:
        raise ConfigError(problems)

    return Config(
        office=office,
        categories=categories,
        fields=fields,
        rules=rules,
        cta_templates={k: str(v).strip() for k, v in templates.items()},
        office_footer=str(bridges.get("office_footer") or ""),
        sources=sources,
        filters=filters,
        scoring=scoring,
        warnings=warnings,
    )
