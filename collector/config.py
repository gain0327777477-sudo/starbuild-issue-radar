"""config/*.yaml 읽기와 검사.

대표님이 설정 파일을 고치다 실수해도 원인을 바로 알 수 있도록,
문제가 있으면 '어느 파일의 어느 항목이 왜 틀렸는지'를 한국어로 모두 모아서 알려준다.
- problems: 이대로는 수집기가 돌 수 없는 문제 → ConfigError
- warnings: 돌 수는 있지만 확인이 필요한 것 (빈칸, 보장성 표현, 하드코딩된 연락처 등)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .schedule import cron_kst_hours, kst_to_utc_hours, workflow_cron

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

FILES = ("office", "blog_categories", "keywords", "bridges", "sources", "filters", "scoring", "stages", "regions",
         "directions", "packet", "press")
DIRECTION_KEYS = ("up", "down", "hold", "unclear")
GROUPS = ("집객형", "사업형")
TABS = ("증시·경제", "금리·대출", "숙박·관광", "부동산·상권", "지역개발")
STRENGTHS = ("직접", "간접")
POLARITIES = ("호재", "악재", "양면")
SENTENCE_PLACEHOLDERS = {"지역"}
OFFICE_KEYS = ("name", "short_name", "representative", "registration_no", "address", "phone", "mobile", "kakao", "blog")


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
    company_combos: dict
    agencies: list[list[str]]
    rules: list[dict]
    cta_templates: dict[str, str]
    office_footer: str
    soft_cta_fields: list[str]
    single_stock: dict
    sources: dict
    filters: dict
    scoring: dict
    stages: dict
    regions: dict
    directions: dict
    packet: dict
    press: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def category_names(self) -> list[str]:
        return [c["name"] for c in self.categories]

    @property
    def blog_rss_url(self) -> str | None:
        m = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)", self.office.get("blog", ""))
        return f"https://rss.blog.naver.com/{m.group(1)}.xml" if m else None

    def all_keywords(self) -> list[tuple[str, Keyword]]:
        return [(f.id, k) for f in self.fields.values() for k in f.keywords]

    def combo_queries(self) -> list[tuple[str, str]]:
        """대기업 × 동작 조합 검색어 [(분야, '삼성 착공'), ...]"""
        c = self.company_combos
        if not c:
            return []
        return [(c["field"], f"{co.query} {act.query}") for co in c["companies"] for act in c["actions"]]

    def naver_queries(self) -> list[tuple[str, str]]:
        return [(fid, k.query) for fid, k in self.all_keywords()] + self.combo_queries()

    def agency_terms(self, name: str) -> list[str]:
        """'재정경제부' → ['재정경제부', '기획재정부', '기재부', '재경부'] (목록에 없으면 [name])"""
        for terms in self.agencies:
            if terms[0] == name:
                return terms
        return [name]


def split_terms(raw) -> list[str]:
    return [t.strip() for t in str(raw).split("|") if t.strip()]


def parse_keyword(raw) -> Keyword:
    terms = split_terms(raw)
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


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def find_phrases(text: str, phrases: list[str], allowed: list[str] = ()) -> list[str]:
    """text 안의 금지 표현을 찾는다. 금지 표현 안의 띄어쓰기는 있어도 없어도 찾음('대출 가능'→'대출가능'도).
    단, 서로 다른 낱말을 이어 붙여 찾지는 않음('착공 포항' 안에서 '공포'를 찾지 않게).
    allowed 에 든 말은 먼저 지우고 찾는다."""
    t = text
    for a in allowed:
        t = t.replace(a, " ")
    hits = []
    for p in phrases:
        words = p.split()
        if words and re.search(r"\s*".join(re.escape(w) for w in words), t):
            hits.append(p)
    return hits


PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[-.)\s]\d{3,4}[-.\s]\d{4}(?!\d)|(?<!\d)01[016789]\d{7,8}(?!\d)")
REGNO_RE = re.compile(r"등록번호\s*[:：]?\s*(제\s*)?\d")
SCAN_GLOBS = ("config/*.yaml", "collector/**/*.py", "web/**/*.html", "web/**/*.js", "web/**/*.css",
              "web/**/*.json", "README_KO.md", ".github/workflows/*.yml")


def scan_hardcoded_office_info(root: Path, office: dict) -> list[str]:
    """office.yaml 이 아닌 곳에 전화번호·등록번호·주소가 직접 적혀 있으면 찾아낸다."""
    values = [office.get(k, "") for k in ("registration_no", "address", "phone", "mobile", "kakao")]
    values = [v for v in values if len(v) >= 5]
    found = []
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.name == "office.yaml" or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if PHONE_RE.search(line) or REGNO_RE.search(line) or any(v in line for v in values):
                    found.append(f"{rel} {n}번째 줄")
    return found


def load_config(config_dir: Path = CONFIG_DIR, scan_root: Path | None = ROOT) -> Config:
    problems: list[str] = []
    warnings: list[str] = []
    raw = {name: _read_yaml(config_dir / f"{name}.yaml", problems) for name in FILES}

    # office.yaml — 비어 있어도 동작은 하되 경고
    office = {k: ("" if v is None else str(v).strip()) for k, v in raw["office"].items()}
    for k in OFFICE_KEYS:
        office.setdefault(k, "")
    blank = [k for k in ("name", "registration_no", "address", "phone") if not office[k]]
    if blank:
        warnings.append(f"office.yaml: {', '.join(blank)} 이(가) 비어 있습니다 — 상담 안내 문구에서 빠집니다")

    # blog_categories.yaml
    categories = raw["blog_categories"].get("categories") or []
    names = []
    for i, c in enumerate(categories, 1):
        if not isinstance(c, dict) or not c.get("name"):
            problems.append(f"blog_categories.yaml: {i}번째 카테고리에 name 이 없습니다")
            continue
        c["url"] = c.get("url") or ""
        names.append(c["name"])
    if len(names) != len(set(names)):
        problems.append("blog_categories.yaml: 같은 카테고리 이름이 두 번 있습니다")
    empty_urls = [c["name"] for c in categories if isinstance(c, dict) and c.get("name") and not c["url"]]
    if empty_urls:
        warnings.append(f"blog_categories.yaml: URL 미입력 {len(empty_urls)}개 (나중에 입력해도 됩니다)")

    # keywords.yaml
    kw_raw = raw["keywords"]
    fields: dict[str, Field] = {}
    for fid, f in (kw_raw.get("fields") or {}).items():
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

    combos = kw_raw.get("company_combos") or {}
    if combos:
        if combos.get("field") not in fields:
            problems.append(f"keywords.yaml의 company_combos: field '{combos.get('field')}' 는 없는 분야입니다")
        combos = {
            "field": combos.get("field"),
            "companies": [parse_keyword(c) for c in combos.get("companies") or []],
            "actions": [parse_keyword(a) for a in combos.get("actions") or []],
        }
        if not combos["companies"] or not combos["actions"]:
            problems.append("keywords.yaml의 company_combos: companies 와 actions 가 모두 있어야 합니다")

    agencies = [split_terms(a) for a in kw_raw.get("agencies") or []]
    agencies = [a for a in agencies if a]

    # stages.yaml
    stages = raw["stages"]
    stage_ids_by_field: dict[str, set[str]] = {}
    for group_name in ("development", "transport"):
        g = stages.get(group_name) or {}
        where = f"stages.yaml의 {group_name}"
        ids = [s.get("id") for s in g.get("stages") or []]
        if not ids:
            problems.append(f"{where}: stages 가 비어 있습니다")
        if len(ids) != len(set(ids)):
            problems.append(f"{where}: 단계 id 가 중복됩니다")
        for s in g.get("stages") or []:
            if not s.get("words"):
                problems.append(f"{where}의 {s.get('id')}: words 가 비어 있습니다")
            if s.get("cautious") and not s.get("notice"):
                problems.append(f"{where}의 {s.get('id')}: 신중 단계(cautious)에는 notice(안내 문장)가 필요합니다")
            s["words"] = [str(w) for w in s.get("words") or []]
            s["points"] = s.get("points") or 0
            s["notice"] = s.get("notice") or ""
        if stages.get("default_stage") not in ids:
            problems.append(f"stages.yaml: default_stage '{stages.get('default_stage')}' 가 {group_name} 단계 목록에 없습니다")
        for fid in g.get("fields") or []:
            if fid not in fields:
                problems.append(f"{where}: fields 의 '{fid}' 는 없는 분야입니다")
            stage_ids_by_field.setdefault(fid, set()).update(ids)
    for key in ("delay_words", "assertive_words", "assertive_allowed", "future_words", "future_prefix_words",
                "ignore_after_words", "document_words", "future_prefix_patterns",
                "ignore_after_patterns"):
        stages[key] = [str(w) for w in stages.get(key) or []]
    stages["upcoming_notice"] = str(stages.get("upcoming_notice") or "")
    for pat in stages["future_prefix_patterns"] + stages["ignore_after_patterns"]:
        try:
            re.compile(pat)
        except re.error as e:
            problems.append(f"stages.yaml의 future_prefix_patterns: '{pat}' 형식 오류 ({e})")

    # regions.yaml
    reg_raw = raw["regions"]
    regions = {}
    for sido, r in (reg_raw.get("regions") or {}).items():
        if not isinstance(r, dict) or not r.get("names"):
            problems.append(f"regions.yaml의 {sido}: names 가 비어 있습니다")
            continue
        regions[sido] = {"names": [str(n) for n in r["names"]], "areas": [split_terms(a) for a in r.get("areas") or []]}
    combined = {}
    for alias, sidos in (reg_raw.get("combined_aliases") or {}).items():
        unknown = [s for s in sidos or [] if s not in regions]
        if unknown or not sidos:
            problems.append(f"regions.yaml의 combined_aliases '{alias}': 없는 시·도 {unknown}")
        combined[str(alias)] = [str(s) for s in sidos or []]
    known_places = set(regions)
    for r in regions.values():
        for terms in r["areas"]:
            for t in terms:
                known_places.add(t)
                known_places.add(re.sub(r"[시군구]$", "", t))
    tourist = [str(x) for x in reg_raw.get("tourist_areas") or []]
    for t in tourist:
        if t not in known_places:
            problems.append(f"regions.yaml의 tourist_areas: '{t}' 는 시·도나 시·군·구 이름이 아닙니다")
    regions = {
        "regions": regions,
        "combined_aliases": combined,
        "tourist_areas": tourist,
        "require_full_name": [str(x) for x in reg_raw.get("require_full_name") or []],
        "ignore_phrases": [str(x) for x in reg_raw.get("ignore_phrases") or []],
    }

    # press.yaml
    press_map = {}
    for d, n in (raw["press"].get("press") or {}).items():
        if not str(n or "").strip():
            problems.append(f"press.yaml: '{d}' 의 언론사 이름이 비어 있습니다")
        press_map[str(d).lower().removeprefix("www.")] = str(n).strip()

    # directions.yaml
    directions = raw["directions"]
    topics = directions.get("topics") or {}
    for tid, t in topics.items():
        if not isinstance(t, dict) or not t.get("labels") or not t.get("words"):
            problems.append(f"directions.yaml의 {tid}: labels 와 words 가 있어야 합니다")
            continue
        t["phrases"] = {d: [str(p) for p in v or []] for d, v in (t.get("phrases") or {}).items()}
        t["words"] = {d: [str(w) for w in v or []] for d, v in (t.get("words") or {}).items()}
        for d in list(t["phrases"]) + list(t["words"]):
            if d not in ("up", "down", "hold"):
                problems.append(f"directions.yaml의 {tid}: '{d}' 는 up / down / hold 중 하나여야 합니다")
    directions["topics"] = topics

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
        r["also_words"] = [str(w) for w in (r.get("also_words") or [])]
        r["stages"] = [str(s) for s in (r.get("stages") or [])]
        rule_fields = r.get("fields") or []
        for fid in rule_fields:
            if fid not in fields:
                problems.append(f"{where}: fields 의 '{fid}' 는 keywords.yaml 에 없는 분야입니다")
        if not rule_fields:
            problems.append(f"{where}: fields 가 비어 있습니다")
        for sid in r["stages"]:
            if not any(sid in stage_ids_by_field.get(fid, ()) for fid in rule_fields):
                problems.append(f"{where}: stages 의 '{sid}' 는 이 분야들의 진행 단계(stages.yaml)에 없습니다")
        for cat in r.get("categories") or []:
            if cat not in names:
                problems.append(f"{where}: 카테고리 '{cat}' 는 blog_categories.yaml 에 없습니다 (이름을 똑같이 써야 합니다)")
        if not r.get("categories"):
            problems.append(f"{where}: categories 가 비어 있습니다")
        if r.get("strength") not in STRENGTHS:
            problems.append(f"{where}: strength 는 직접 / 간접 중 하나여야 합니다 (지금: {r.get('strength')})")
        if r.get("polarity") is not None and r.get("polarity") not in POLARITIES:
            problems.append(f"{where}: polarity 는 호재 / 악재 / 양면 중 하나여야 합니다 (지금: {r.get('polarity')})")
        if "transport" in rule_fields and not r.get("polarity"):
            problems.append(f"{where}: 교통 인프라 규칙에는 polarity(호재/악재/양면)가 꼭 있어야 합니다")
        if r.get("cta") not in templates:
            problems.append(f"{where}: cta '{r.get('cta')}' 에 해당하는 문구가 cta_templates 에 없습니다")
        r["cta_if_words"] = {str(k): [str(w) for w in v or []] for k, v in (r.get("cta_if_words") or {}).items()}
        for k in r["cta_if_words"]:
            if k not in templates:
                problems.append(f"{where}: cta_if_words 의 '{k}' 에 해당하는 문구가 cta_templates 에 없습니다")
        r["notice"] = str(r.get("notice") or "")
        r["tourist_bonus"] = r.get("tourist_bonus") or 0
        if not isinstance(r["tourist_bonus"], (int, float)):
            problems.append(f"{where}: tourist_bonus 는 숫자여야 합니다")
        for key in ("logic", "sentence"):
            if not r.get(key):
                problems.append(f"{where}: {key} 가 비어 있습니다")
        for ph in set(re.findall(r"\{([^}]*)\}", str(r.get("sentence") or ""))) - SENTENCE_PLACEHOLDERS:
            problems.append(f"{where}: sentence 의 {{{ph}}} 는 쓸 수 없는 자리표시입니다 ({{지역}} 만 가능)")
        dconf = r.get("direction")
        if dconf:
            if dconf.get("topic") not in topics:
                problems.append(f"{where}: direction.topic '{dconf.get('topic')}' 는 directions.yaml 에 없습니다")
            dconf["applies_if"] = [str(w) for w in dconf.get("applies_if") or []]
            for d in DIRECTION_KEYS:
                block = dconf.get(d)
                if block is None:
                    continue
                for cat in block.get("categories") or []:
                    if cat not in names:
                        problems.append(f"{where}의 direction.{d}: 카테고리 '{cat}' 가 blog_categories.yaml 에 없습니다")
                if block.get("cta") and block["cta"] not in templates:
                    problems.append(f"{where}의 direction.{d}: cta '{block['cta']}' 문구가 없습니다")
            if "unclear" not in dconf:
                problems.append(f"{where}: direction 에는 unclear(방향 불분명일 때 중립 문장)가 꼭 있어야 합니다")
            extra = set(dconf) - set(DIRECTION_KEYS) - {"topic", "applies_if"}
            if extra:
                problems.append(f"{where}: direction 에 알 수 없는 항목 {sorted(extra)}")
        r["direction"] = dconf or None

    single = bridges.get("single_stock") or {}
    if single:
        for key in ("words", "logic", "sentence", "categories", "cta"):
            if not single.get(key):
                problems.append(f"bridges.yaml의 single_stock: {key} 가 비어 있습니다")
        for cat in single.get("categories") or []:
            if cat not in names:
                problems.append(f"bridges.yaml의 single_stock: 카테고리 '{cat}' 가 blog_categories.yaml 에 없습니다")
        if single.get("cta") and single["cta"] not in templates:
            problems.append(f"bridges.yaml의 single_stock: cta '{single['cta']}' 문구가 없습니다")
        single["companies"] = [str(c) for c in single.get("companies") or []]
        single["words"] = [str(w) for w in single.get("words") or []]
    soft_fields = [str(f) for f in bridges.get("soft_cta_fields") or []]
    for fid in soft_fields:
        if fid not in fields:
            problems.append(f"bridges.yaml의 soft_cta_fields: '{fid}' 는 없는 분야입니다")
    if soft_fields and "soft" not in templates:
        problems.append("bridges.yaml: soft_cta_fields 가 있으면 cta_templates 에 soft 문구가 있어야 합니다")
    for k, v in list(templates.items()) + [("office_footer", bridges.get("office_footer") or "")]:
        for ph in set(re.findall(r"\{([^}]*)\}", str(v))) - set(OFFICE_KEYS):
            problems.append(f"bridges.yaml의 {k}: {{{ph}}} 는 office.yaml 에 없는 항목입니다")

    # sources.yaml
    sources = raw["sources"]
    hours = sources.get("schedule_kst_hours")
    if not isinstance(hours, list) or not hours or not all(isinstance(h, int) and 0 <= h <= 23 for h in hours)             or len(set(hours)) != len(hours):
        problems.append("sources.yaml: schedule_kst_hours 는 0~23 사이 정각 목록이어야 합니다 (예: [6, 8, 10])")
    else:
        sources["schedule_kst_hours"] = sorted(hours)
        cron = workflow_cron()
        if cron is not None and cron_kst_hours(cron) != sorted(hours):
            warnings.append(f"수집 일정 불일치: sources.yaml 은 한국 {sorted(hours)}시인데 워크플로 cron '{cron}' 은 한국 "
                            f"{cron_kst_hours(cron)}시입니다 → cron 을 '0 {','.join(map(str, kst_to_utc_hours(hours)))} * * *' 로 바꾸세요")
    q = sources.get("quiet_hours") or {}
    if not all(isinstance(q.get(k), int) and 0 <= q[k] <= 23 for k in ("start", "end")):
        problems.append("sources.yaml: quiet_hours 는 {start: 22, end: 7} 처럼 적어야 합니다")
    if not isinstance(sources.get("stale_hours"), (int, float)) or sources["stale_hours"] <= 0:
        problems.append("sources.yaml: stale_hours 는 0보다 큰 숫자여야 합니다")
    dl = sources.get("datalab") or {}
    if dl.get("groups_per_call", 5) > 5:
        problems.append("sources.yaml: datalab.groups_per_call 은 최대 5입니다 (네이버 규칙)")
    if dl.get("daily_call_limit", 0) > 1000:
        problems.append("sources.yaml: datalab.daily_call_limit 은 네이버 한도 1,000 이하로 두세요")
    if (sources.get("naver_news") or {}).get("daily_call_limit", 0) > 25000:
        problems.append("sources.yaml: naver_news.daily_call_limit 은 네이버 한도 25,000 이하로 두세요")
    gn = sources.get("google_news") or {}
    for s in (gn.get("local_gov") or {}).get("sites") or []:
        if not isinstance(s, dict) or not s.get("site") or not s.get("name"):
            problems.append("sources.yaml: google_news.local_gov.sites 의 각 줄에는 name 과 site 가 있어야 합니다")
    if (sources.get("blog_rss") or {}).get("enabled"):
        if not re.search(r"blog\.naver\.com/[A-Za-z0-9_-]+", office["blog"]):
            problems.append("office.yaml: blog 주소가 'https://blog.naver.com/아이디' 형식이 아닙니다 (블로그 RSS 주소를 만들 수 없음)")

    # filters.yaml — 목록 항목만 정리
    filters = {k: (v if isinstance(v, bool) else [str(x) for x in (v or [])]) for k, v in raw["filters"].items()}

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

    # ── 문구 검사 (경고) ─────────────────────
    guarantee = filters.get("guarantee_phrases") or []
    fear = filters.get("fear_phrases") or []
    texts = [(f"cta_templates.{k}", str(v)) for k, v in templates.items()]
    texts.append(("office_footer", str(bridges.get("office_footer") or "")))
    if single:
        texts += [("single_stock logic", str(single["logic"])), ("single_stock sentence", str(single["sentence"]))]
    for r in rules:
        texts += [(f"규칙 {r['id']} logic", str(r["logic"])), (f"규칙 {r['id']} sentence", str(r["sentence"])),
                  (f"규칙 {r['id']} notice", r["notice"])]
        for d in DIRECTION_KEYS:
            block = (r["direction"] or {}).get(d) or {}
            for key in ("logic", "sentence"):
                if block.get(key):
                    texts.append((f"규칙 {r['id']} {d}.{key}", str(block[key])))
    for where, text in texts:
        clean = text
        for cat in names:          # '대출 가능·불가 판단' 같은 카테고리 이름 자체는 검사에서 뺌
            clean = clean.replace(cat, "")
        hits = find_phrases(clean, guarantee)
        if hits:
            warnings.append(f"bridges.yaml {where}: 보장성 표현 {hits} — 빼거나 바꿔 주세요")
        hits = find_phrases(clean, fear)
        if hits:
            warnings.append(f"bridges.yaml {where}: 공포 조장 표현 {hits} — '확인할 것' 중심으로 바꿔 주세요")

    cautious_ids = {s["id"] for g in ("development", "transport") for s in stages[g]["stages"] if s.get("cautious")}
    for r in rules:
        applies_to_cautious = not r["stages"] or bool(set(r["stages"]) & cautious_ids)
        if applies_to_cautious:
            all_sentences = " ".join([str(r["sentence"])] + [str(((r["direction"] or {}).get(d) or {}).get("sentence", ""))
                                                          for d in DIRECTION_KEYS])
            hits = find_phrases(all_sentences, stages["assertive_words"], stages["assertive_allowed"])
            if hits:
                warnings.append(f"bridges.yaml 규칙 {r['id']} sentence: 계획 단계에도 쓰이는 문장인데 단정 표현 {hits} 이 있습니다")

    if scan_root is not None:
        spots = scan_hardcoded_office_info(scan_root, office)
        if spots:
            warnings.append("사무소 연락처·등록번호·주소로 보이는 값이 office.yaml 밖에 있습니다 → office.yaml 로 옮기세요: "
                            + ", ".join(spots))

    return Config(
        office=office,
        categories=categories,
        fields=fields,
        company_combos=combos,
        agencies=agencies,
        rules=rules,
        cta_templates={k: str(v).strip() for k, v in templates.items()},
        office_footer=str(bridges.get("office_footer") or ""),
        soft_cta_fields=soft_fields,
        single_stock=single,
        sources=sources,
        filters=filters,
        scoring=scoring,
        stages=stages,
        regions=regions,
        directions=directions,
        packet=raw["packet"],
        press=press_map,
        warnings=warnings,
    )
