"""이슈 → 블로그 카테고리 연결 (설정: config/bridges.yaml)."""
from __future__ import annotations

from .config import Config
from .direction import detect as detect_direction
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


def apply_direction(rule: dict, rep_title: str, other_titles: list[str], cfg: Config) -> tuple[dict | None, dict]:
    """방향 규칙이면 (방향 판정 결과, 그 방향의 문장 묶음). 방향 판정 대상이 아니면 (None, {})."""
    dconf = rule.get("direction")
    if not dconf:
        return None, {}
    titles = " ".join([rep_title] + other_titles)
    if dconf["applies_if"] and not any(contains(titles, w) for w in dconf["applies_if"]):
        return None, {}
    d = detect_direction(dconf["topic"], rep_title, other_titles, cfg)
    block = dconf.get(d["id"]) or dconf["unclear"]
    if d["id"] not in dconf:
        d = {**d, "id": "unclear", "label": cfg.directions["topics"][dconf["topic"]]["labels"].get("unclear", "방향 불분명")}
    return d, block


def single_stock_match(rep_title: str, fields: list[str], cfg: Config) -> dict | None:
    """증시·경제 분야인데 제목에 회사 이름 + 배당·실적·주가 단어 → {'company': …, 'event': …}"""
    ss = cfg.single_stock
    if not ss or not fields or not (fields[0] in cfg.soft_cta_fields or "stock" in fields):
        return None
    companies = list(ss["companies"])
    for co in (cfg.company_combos or {}).get("companies", []):
        companies += co.terms
    company = next((c for c in sorted(set(companies), key=len, reverse=True) if contains(rep_title, c)), None)
    event = next((w for w in sorted(ss["words"], key=len, reverse=True) if w in rep_title), None)
    return {"company": company, "event": event} if company and event else None


def connect(fields: list[str], text: str, stage: dict | None, region_tags: list[dict], place: str, cfg: Config,
            rep_title: str = "", other_titles: list[str] | None = None) -> dict:
    """fields: 이 이슈의 분야(주 분야 먼저). text: 묶인 기사 제목·요약. place: 연결 문장에 넣을 지역 이름.
    rep_title·other_titles: 방향 판정(환율·금리·증시·예금)에 쓰는 대표 제목과 다른 기사 제목."""
    primary_field = fields[0] if fields else ""

    single = single_stock_match(rep_title, fields, cfg)
    if single:                                  # 개별 종목 → '자금 흐름' 각도로 고정, 안내는 soft
        ss = cfg.single_stock
        return {
            "rule_id": "single_stock", "strength": ss.get("strength", "간접"), "categories": list(ss["categories"]),
            "other_categories": [], "logic": ss["logic"], "sentence": ss["sentence"], "cta": ss["cta"],
            "polarity": "", "direction": None, "notices": [], "tourist_bonus": 0, "single_stock": single,
        }
    matched = [(i, r) for i, r in enumerate(cfg.rules) if _matches(r, fields, text, stage)]
    # 주 분야 규칙 먼저 → 요약에 '한은'이 스쳐 지나간 환율 기사가 '대출 판단(직접)'으로 끌려가지 않게
    matched.sort(key=lambda ir: (primary_field not in ir[1]["fields"], STRENGTH_RANK[ir[1]["strength"]], ir[0]))

    if not matched:
        polarity = "양면" if "transport" in fields else ""
        return {
            "rule_id": "", "strength": "약함", "categories": ["뉴스"], "other_categories": [],
            "logic": WEAK_LOGIC, "sentence": "", "cta": choose_cta(None, "약함", primary_field, text, cfg),
            "polarity": polarity, "direction": None, "notices": [], "tourist_bonus": 0,
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

    direction, block = apply_direction(rule, rep_title, other_titles or [], cfg)
    categories = list(block.get("categories") or rule["categories"])
    logic = block.get("logic") or rule["logic"]
    sentence = block.get("sentence") or rule["sentence"]
    tourist = block["tourist_bonus"] if "tourist_bonus" in block else rule["tourist_bonus"]
    if direction and direction["id"] == "unclear":
        cta = "soft" if "soft" in cfg.cta_templates else rule["cta"]   # 방향 불분명 → 중립 문장 + 부드러운 안내
    elif block.get("cta"):
        cta = block["cta"]
    else:
        cta = choose_cta(rule, rule["strength"], primary_field, text, cfg)

    notices = [n for n in (rule["notice"], stage["notice"] if stage else "") if n]
    bonus = tourist if tourist and is_tourist(region_tags, cfg) else 0
    return {
        "rule_id": rule["id"],
        "strength": rule["strength"],
        "categories": categories,
        "other_categories": [c for c in others if c not in categories][:3],
        "logic": logic,
        "sentence": fill_place(sentence, place or "해당 지역"),
        "cta": cta,
        "polarity": polarity,
        "direction": direction,
        "notices": notices,
        "tourist_bonus": bonus,
    }
