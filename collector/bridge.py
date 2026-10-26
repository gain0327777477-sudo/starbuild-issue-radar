"""이슈 → 블로그 카테고리 연결 (설정: config/bridges.yaml)."""
from __future__ import annotations

from .config import Config
from .text import contains, fill_place

STRENGTH_RANK = {"직접": 0, "간접": 1, "약함": 2}
WEAK_LOGIC = "내 블로그 카테고리와 바로 이어지는 연결 규칙이 없습니다 (연결 약함)"


def _matches(rule: dict, fields: list[str], text: str, stage: dict | None) -> bool:
    if not set(rule["fields"]) & set(fields):
        return False
    if rule["words"] and not any(contains(text, w) for w in rule["words"]):
        return False
    if rule["also_words"] and not any(contains(text, w) for w in rule["also_words"]):
        return False
    if rule["stages"] and (not stage or stage["id"] not in rule["stages"]):
        return False
    return True


def is_tourist(region_tags: list[dict], cfg: Config) -> bool:
    places = set(cfg.regions["tourist_areas"])
    for t in region_tags:
        area_stem = t["area"][:-1] if t["area"][-1:] in "시군구" else t["area"]
        if t["sido"] in places or t["area"] in places or area_stem in places:
            return True
    return False


def choose_cta(rule: dict | None, strength: str, primary_field: str, text: str, cfg: Config) -> str:
    """집객형 분야(soft_cta_fields)는 '직접' 연결일 때만 규칙의 상담 안내, 아니면 soft."""
    if rule:
        for cta, words in rule["cta_if_words"].items():
            if any(contains(text, w) for w in words):
                return cta
    if rule is None:
        return "soft" if "soft" in cfg.cta_templates else "listing"
    if primary_field in cfg.soft_cta_fields and strength != "직접":
        return "soft"
    return rule["cta"]


def connect(fields: list[str], text: str, stage: dict | None, region_tags: list[dict], place: str, cfg: Config) -> dict:
    """fields: 이 이슈의 분야(주 분야 먼저). text: 묶인 기사 제목·요약. place: 연결 문장에 넣을 지역 이름."""
    primary_field = fields[0] if fields else ""
    matched = [(i, r) for i, r in enumerate(cfg.rules) if _matches(r, fields, text, stage)]
    # 주 분야 규칙 먼저 → 요약에 '한은'이 스쳐 지나간 환율 기사가 '대출 판단(직접)'으로 끌려가지 않게
    matched.sort(key=lambda ir: (primary_field not in ir[1]["fields"], STRENGTH_RANK[ir[1]["strength"]], ir[0]))

    if not matched:
        polarity = "양면" if "transport" in fields else ""
        return {
            "rule_id": "", "strength": "약함", "categories": ["뉴스"], "other_categories": [],
            "logic": WEAK_LOGIC, "sentence": "", "cta": choose_cta(None, "약함", primary_field, text, cfg),
            "polarity": polarity, "notices": [], "tourist_bonus": 0,
        }

    rule = matched[0][1]
    others = []
    for _, r in matched[1:]:
        for c in r["categories"]:
            if c not in rule["categories"] and c not in others:
                others.append(c)

    polarity = rule.get("polarity") or ("양면" if "transport" in fields else "")
    if polarity == "호재" and stage and stage.get("delayed"):
        polarity = "양면"                       # 개통 연기·무산 소식이면 호재로 단정하지 않음

    notices = [n for n in (rule["notice"], stage["notice"] if stage else "") if n]
    bonus = rule["tourist_bonus"] if rule["tourist_bonus"] and is_tourist(region_tags, cfg) else 0
    return {
        "rule_id": rule["id"],
        "strength": rule["strength"],
        "categories": list(rule["categories"]),
        "other_categories": others[:3],
        "logic": rule["logic"],
        "sentence": fill_place(rule["sentence"], place or "해당 지역"),
        "cta": choose_cta(rule, rule["strength"], primary_field, text, cfg),
        "polarity": polarity,
        "notices": notices,
        "tourist_bonus": bonus,
    }
