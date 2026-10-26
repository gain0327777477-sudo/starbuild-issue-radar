"""4단계: 블로그 소재 패킷 (설정: config/packet.yaml).

이슈 하나 → 제목 후보 3개, 핵심 키워드 5~7개, 검색 의도, 글 틀, 연결 카테고리·문장, 상담 안내,
필수 문구, 출처 + 네이버 에디터용 일반 텍스트 + Claude 앱에 붙일 '원고 작성 요청문'.
기사 본문은 쓰지 않는다(제목·링크·소스가 준 짧은 요약만).
"""
from __future__ import annotations

import re
from datetime import datetime

from .classify import region_label
from .config import Config, find_phrases
from .text import contains

SECTION = "■ "


# ── 작은 도구 ─────────────────────────────────

def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y.%m.%d")
    except ValueError:
        return iso[:10]


def clean_headline(title: str) -> str:
    """기사 제목 → 제목 후보용 요지: [속보]·(종합)·따옴표·언론사 꼬리 제거, 첫 구절만."""
    t = re.sub(r"\[[^\]]{1,12}\]|【[^】]{1,12}】|\((종합|속보|단독|\d보|상보)\)", " ", title)
    t = re.sub(r"\s+-\s+[^-]{1,15}$", "", t)                       # ' - 머니투데이'
    t = re.sub(r"[\"'“”‘’]", "", t)
    t = re.split(r"…|\.\.\.|‥|\s\|\s", t)[0]
    return re.sub(r"\s+", " ", t).strip(" ,·")


def fit(text: str, max_chars: int) -> str:
    """max_chars 안으로: 넘치면 마지막 띄어쓰기에서 자르고 끝의 쉼표 등 정리."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if " " in cut[max_chars // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,·…:")


def _fill(template: str, keyword: str, place: str) -> str:
    return template.replace("{키워드}", keyword).replace("{지역}", f"{place} " if place else "").replace("  ", " ")


def _office_line(line: str, office: dict) -> str:
    """'전화 {phone} / 휴대폰 {mobile}' 에서 빈 값이 든 조각은 뺌. 모두 비면 ''."""
    parts = re.split(r"(\s\|\s|\s/\s)", line)
    kept = []
    for p in parts[::2]:
        keys = re.findall(r"\{(\w+)\}", p)
        if keys and not all(office.get(k) for k in keys):
            continue
        kept.append(re.sub(r"\{(\w+)\}", lambda m: office.get(m.group(1), ""), p).strip())
    return " | ".join(k for k in kept if k)


def cta_text(cta: str, cfg: Config) -> tuple[str, str]:
    office = dict(cfg.office)
    office["short_name"] = office.get("short_name") or office.get("name") or "저희 사무소"
    body = re.sub(r"\{(\w+)\}", lambda m: office.get(m.group(1), ""), cfg.cta_templates.get(cta, ""))
    footer = "\n".join(x for x in (_office_line(line, office) for line in cfg.office_footer.splitlines()) if x)
    return body.strip(), footer


def banned_lists(cfg: Config, cautious: bool) -> dict[str, list[str]]:
    out = {
        "보장성 표현": cfg.filters.get("guarantee_phrases") or [],
        "공포 조장 표현": cfg.filters.get("fear_phrases") or [],
        "과장·낚시 표현": cfg.packet.get("hype_words") or [],
    }
    if cautious:
        out["단정 표현(계획·예정 단계라 쓰지 않음)"] = cfg.stages.get("assertive_words") or []
    return out


def guard(texts: list[str], cfg: Config, cautious: bool) -> list[str]:
    """우리가 만든 문장에 금지 표현이 있는지. 카테고리 이름('대출 가능·불가 판단')은 빼고 검사."""
    joined = "\n".join(texts)
    for cat in cfg.category_names:
        joined = joined.replace(cat, "")
    warnings = []
    for label, words in banned_lists(cfg, cautious).items():
        allowed = (cfg.stages.get("assertive_allowed") or []) if label.startswith("단정") else []
        hits = find_phrases(joined, words, allowed)
        if hits:
            warnings.append(f"{label}: {', '.join(hits)}")
    return warnings


# ── 패킷 만들기 ───────────────────────────────

def build_packet(issue: dict, cfg: Config) -> dict:
    pc = cfg.packet
    br = issue["bridge"]
    stage = issue.get("stage") or {}
    direction = br.get("direction") or {}
    cautious = bool(stage.get("cautious"))
    field = cfg.fields.get(issue["primary_field"])

    # 핵심 키워드: '착공·준공' 같은 일반 낱말보다 구체적인 키워드(GTX·재건축 등)를 먼저
    generic = set(pc.get("generic_keywords") or [])
    core = issue.get("core_keywords") or []
    keyword = next((k for k in core if k not in generic), core[0] if core else None) \
        or (field.keywords[0].query if field else issue["field_name"])
    place = region_label(issue.get("region_tags") or [])
    suffix = ""
    if direction and direction.get("id") not in (None, "unclear"):
        suffix = ((cfg.directions.get("title_suffix") or {}).get(direction["topic"]) or {}).get(direction["id"], "")
    elif stage and not cautious:
        suffix = stage["name"]
        # '도로 개설'·'공장 증설'처럼 키워드에 이미 동작 말이 있으면 '개통' 등을 덧붙이지 않음
        stage_like = [w for g in ("development", "transport") for s in cfg.stages[g]["stages"] for w in s["words"]]
        if any(w in keyword for w in stage_like + list(pc.get("action_words") or [])):
            suffix = ""
    kw_full = f"{keyword} {suffix}".strip() if suffix and suffix not in keyword else keyword

    category = br["categories"][0]
    cq = (pc.get("category_questions") or {}).get(category) or pc["category_questions"]["뉴스"]
    single = br.get("single_stock")              # 개별 종목 → 자금 흐름 각도
    ssc = pc.get("single_stock") or {}
    if single and ssc:
        question = ssc["question"].replace("{회사}", single["company"]).replace("{사건}", single["event"])
    else:
        question = _fill(cq["question"], kw_full, place)

    # 확인할 것
    checklist = []
    base_items = ssc.get("checklist", []) if single and ssc else (pc.get("checklist") or {}).get(category, [])
    for item in base_items + (pc.get("checklist_by_stage") or {}).get(stage.get("id", ""), []) \
            + (pc.get("checklist_by_polarity") or {}).get(br.get("polarity", ""), []):
        item = _fill(item, keyword, place).strip()
        if item and item not in checklist:
            checklist.append(item)
    checklist = checklist[:6]

    # 제목 후보: 질문형·설명형·확인형 틀에서 하나씩 (키워드를 원래 기사 제목 앞에 억지로 붙이지 않음)
    tconf = pc.get("title") or {}
    max_c, min_c = tconf.get("max_chars", 32), tconf.get("min_chars", 12)

    def fill_title(t: str) -> str:
        values = {"{키워드}": kw_full, "{지역}": f"{place} " if place and place not in kw_full else "",
                  "{명사}": cq.get("noun") or category, "{짧은질문}": cq.get("short", "무엇이 달라지나"),
                  "{개수}": str(len(checklist))}
        if single:
            values.update({"{회사}": single["company"], "{사건}": single["event"]})
        for k, v in values.items():
            t = t.replace(k, v)
        return re.sub(r"\s+", " ", t).strip()

    def usable(t: str) -> bool:
        return min_c <= len(t) <= max_c and not guard([t], cfg, cautious)

    if single and ssc.get("title_templates"):
        groups = [[t] for t in ssc["title_templates"]]
    else:
        groups = [tconf.get("question_templates") or [], tconf.get("explain_templates") or [], tconf.get("check_templates") or []]
    titles: list[str] = []
    for group in groups:
        for tpl in group:
            if "{개수}" in tpl and not checklist:
                continue
            t = fill_title(tpl)
            if usable(t) and t not in titles:
                titles.append(t)
                break
    for tpl in [t for g in groups for t in g]:               # 한 묶음이 모두 안 맞으면 다른 틀로 채움
        if len(titles) >= 3:
            break
        t = fit(fill_title(tpl), max_c)
        if usable(t) and t not in titles:
            titles.append(t)
    titles = titles[:3]

    # 핵심 키워드 5~7개
    kws: list[str] = []

    def add(k: str) -> None:
        k = (k or "").strip()
        if k and k not in kws:
            kws.append(k)

    for k in (issue.get("core_keywords") or [])[:3]:
        add(k)
    if suffix:
        add(kw_full)
    cat_kws = (pc.get("category_keywords") or {}).get(category) or []
    if place:
        add(place)
        add(f"{place} {cat_kws[0].split()[0] if cat_kws else category}")     # 예: '포항 모텔'
    for k in cat_kws:
        add(k)
    if field:
        for kw in field.keywords:
            if len(kws) >= 5:
                break
            add(kw.query)
    kws = kws[:7]

    # 쉬운 설명(용어)
    text_all = " ".join([issue["title"], issue.get("summary", "")] + [a["title"] for a in issue.get("articles", [])])
    glossary = [f"{term}: {desc}" for term, desc in (pc.get("glossary") or {}).items() if contains(text_all, term)][:4]

    # 필수 문구
    mandatory = []
    mconf = pc.get("mandatory") or {}
    is_investment = issue["primary_field"] in (mconf.get("investment_fields") or []) or "stock" in issue.get("fields", [])
    if is_investment:
        mandatory.append(mconf.get("investment", "이 글은 투자 권유가 아닙니다."))
    for n in br.get("notices") or []:
        if n not in mandatory:
            mandatory.append(n)

    body, footer = cta_text(br["cta"], cfg)
    status = [x for x in (
        f"방향: {direction['label']}" if direction else "",
        f"진행 단계: {stage['name']}" + (f" (다음: {stage['next']})" if stage.get("next") else "") + (" · 일정 변경 소식 있음" if stage.get("delayed") else "") if stage else "",
        f"호재/악재: {br['polarity']}" if br.get("polarity") else "",
        f"지역: {', '.join(issue['regions'])}" if issue.get("regions") else "",
    ) if x]

    what = f"{_date(issue['published'])} {issue['press']} 보도: {issue['title']} (관련 기사 {issue['related_count']}건)"
    if issue.get("summary"):
        what += f" / 요약: {issue['summary']}"
    view = cq.get("view") or category
    subheads = [f"{kw_full}, 무슨 일이 있었나?", "왜 중요한가?", f"{view} 입장에서는 무엇이 달라지나?", "지금 무엇을 확인해야 할까?"]
    outline = [
        {"heading": "첫 문단 — 답 먼저", "guide": f"'{question}'에 대한 답을 2~3문장으로 먼저 씁니다. 핵심: {br['logic']}"},
        {"heading": "질문형 소제목", "guide": " / ".join(subheads)},
        {"heading": "무슨 일", "guide": what + (" · " + " · ".join(status) if status else "")},
        {"heading": "왜 중요", "guide": br["logic"]},
        {"heading": "쉬운 설명", "guide": " / ".join(glossary) if glossary else "어려운 용어가 나오면 처음 나올 때 쉬운 말로 풀어 씁니다."},
        {"heading": "부동산·사업 영향", "guide": br["sentence"] or "내 사업과의 연결이 약한 이슈입니다. 일반 정보 위주로 짧게 씁니다."},
        {"heading": "확인할 것", "guide": " / ".join(checklist) if checklist else "발표 원문과 시행 시기를 확인합니다."},
    ]
    sources = [{"press": a["press"], "date": _date(a["published"]), "url": a["url"], "title": a["title"]}
               for a in (issue.get("articles") or [])[:5]]

    packet = {
        "issue_id": issue["id"],
        "titles": titles,
        "keywords": kws,
        "question": question,
        "outline": outline,
        "categories": br["categories"],
        "other_categories": br.get("other_categories") or [],
        "sentence": br["sentence"],
        "logic": br["logic"],
        "cta": br["cta"],
        "cta_text": body,
        "office_footer": footer,
        "mandatory": mandatory,
        "status": status,
        "checklist": checklist,
        "sources": sources,
        "is_investment": is_investment,
        "cautious": cautious,
        "single_stock": single,
    }
    generated = titles + [question, br["sentence"], br["logic"], body] + mandatory + [o["guide"] for o in outline if o["heading"] != "무슨 일"]
    packet["warnings"] = guard(generated, cfg, cautious)
    packet["plain_text"] = to_plain_text(issue, packet)
    packet["request_prompt"] = to_request_prompt(issue, packet, cfg)
    return packet


# ── 네이버 에디터용 일반 텍스트 (마크다운 기호 없이) ──

def to_plain_text(issue: dict, p: dict) -> str:
    lines = [f"[블로그 소재] {issue['title']}"]
    if p["status"]:
        lines.append(" · ".join(p["status"]))
    lines += ["", SECTION + "제목 후보"] + [f"{i}. {t}" for i, t in enumerate(p["titles"], 1)]
    lines += ["", SECTION + "핵심 키워드", ", ".join(p["keywords"])]
    lines += ["", SECTION + "검색 의도 (사람들이 궁금해하는 질문)", p["question"]]
    lines += ["", SECTION + "글 틀"] + [f"{i}) {o['heading']}: {o['guide']}" for i, o in enumerate(p["outline"], 1)]
    cats = " · ".join(p["categories"]) + (f" (함께: {' · '.join(p['other_categories'])})" if p["other_categories"] else "")
    lines += ["", SECTION + "연결 카테고리", cats]
    if p["sentence"]:
        lines += ["", SECTION + "연결 문장 예시", p["sentence"]]
    lines += ["", SECTION + "마무리 상담 안내", p["cta_text"]]
    if p["office_footer"]:
        lines.append(p["office_footer"])
    if p["mandatory"]:
        lines += ["", SECTION + "반드시 넣을 문구"] + [f"· {m}" for m in p["mandatory"]]
    lines += ["", SECTION + "출처"] + [f"· {s['press']} · {s['date']} · {s['url']}" for s in p["sources"]]
    return "\n".join(lines).strip() + "\n"


# ── Claude 앱에 붙여넣는 원고 작성 요청문 ───────────

def to_request_prompt(issue: dict, p: dict, cfg: Config) -> str:
    pc = cfg.packet
    office = dict(cfg.office)
    office["short_name"] = office.get("short_name") or office.get("name") or "저희 사무소"
    role = re.sub(r"\{(\w+)\}", lambda m: office.get(m.group(1), ""), pc.get("writer_role", ""))
    style = [s.replace("{질문}", p["question"]) for s in pc.get("writing_style") or []]

    L = ["아래 자료로 네이버 블로그 글 원고를 써 주세요.", "", "[역할]", role, "", "[글쓰기 규칙]"]
    L += [f"{i}. {s}" for i, s in enumerate(style, 1)]
    L += ["", "[제목]", "검색 키워드를 앞에 둔 30자 안팎의 제목 1개를 맨 위에 씁니다. 아래 후보 중 고르거나 다듬어도 됩니다. 과장·낚시 제목은 쓰지 않습니다."]
    L += [f"{i}. {t}" for i, t in enumerate(p["titles"], 1)]
    L += ["", "[핵심 키워드] 본문에 자연스럽게 넣어 주세요", ", ".join(p["keywords"])]
    L += ["", "[이 글이 답할 질문]", p["question"]]
    L += ["", "[이슈 자료] 아래 제목·요약·날짜·언론사만 근거로 씁니다"]
    L.append(f"대표 기사: {issue['title']} ({issue['press']}, {_date(issue['published'])})")
    if issue.get("summary"):
        L.append(f"요약: {issue['summary']}")
    for s in p["sources"][1:]:
        L.append(f"관련 기사: {s['title']} ({s['press']}, {s['date']})")
    L.append(f"관련 기사 수: {issue['related_count']}건")
    if p["status"]:
        L.append(" · ".join(p["status"]))
    L += ["", "[글 틀] 이 순서로 씁니다"] + [f"{i}. {o['heading']}: {o['guide']}" for i, o in enumerate(p["outline"], 1)]
    L += ["", "[사업 연결]", f"연결 카테고리: {' · '.join(p['categories'])}"]
    if p["sentence"]:
        L.append(f"이 내용을 '부동산·사업 영향' 부분에 자연스럽게 녹여 씁니다: {p['sentence']}")
    L += ["", "[마무리 상담 안내] 글 맨 끝에 아래 문장을 그대로 넣습니다", p["cta_text"]]
    if p["office_footer"]:
        L.append(p["office_footer"])
    if p["mandatory"]:
        L += ["", "[반드시 넣을 문구] 본문 끝(상담 안내 앞)에 그대로 넣습니다"] + [f"· {m}" for m in p["mandatory"]]
    L += ["", "[쓰면 안 되는 표현]"]
    for label, words in banned_lists(cfg, p["cautious"]).items():
        L.append(f"· {label}: {', '.join(words)}")
    if p["cautious"]:
        L.append("· 계획·예정 단계이므로 '~할 계획이다', '~할 수 있다', '~가 추진된다'처럼 씁니다.")
    if p["is_investment"]:
        L.append("· 개별 종목의 매수·매도 추천, 수익 보장 표현은 쓰지 않습니다. 시장 흐름 해설과 부동산·실물자산 관점 연결만 씁니다.")
    if p.get("single_stock"):
        L.append(f"· {(cfg.packet.get('single_stock') or {}).get('instruction', '')}")
    if issue["bridge"].get("polarity") in ("악재", "양면"):
        L.append("· 좋지 않은 소식이라도 불안을 키우는 표현 대신 '확인할 것' 중심으로 차분하게 씁니다.")
    L += ["", "[출처] 글 끝에 아래처럼 적습니다"] + [f"· {s['press']}, {s['date']}" for s in p["sources"]]
    return "\n".join(L).strip() + "\n"
