"""3단계 테스트 — 해외 필터, 분류, 지역 태그, 진행 단계, 같은 사건 묶기, 연결, 점수, 추천, 데이터랩, 키 없는 상태."""
from datetime import datetime, timedelta

import pytest

from collector import bridge, process
from collector.classify import RegionTagger, classify, detect_stage, region_label
from collector.cluster import agency_map_from, cluster_articles
from collector.config import load_config
from collector.filters import exclusion_reason, foreign_business
from collector.models import KST, Article
from collector.recommend import pick_today, written_match
from collector.scoring import score_issue
from collector.sources import datalab
from collector.sources.base import Context, SkipSource
from collector.text import fill_place

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=KST)


@pytest.fixture(scope="module")
def cfg():
    return load_config(scan_root=None)


@pytest.fixture(scope="module")
def tagger(cfg):
    return RegionTagger.build(cfg)


def art(title, *, press="연합뉴스", domain="yna.co.kr", source="google", hours_ago=1, summary="", field=None, url=None):
    pub = (NOW - timedelta(hours=hours_ago)).isoformat()
    return Article(id=url or title, title=title, url=url or f"https://{domain}/{abs(hash(title))}", press=press, domain=domain,
                   source=source, published=pub, summary=summary, query="q", field=field, fetched_at=NOW.isoformat())


# ── 해외 기사 필터 ───────────────────────────────

@pytest.mark.parametrize("a, expected", [
    (art("호치민시, 190억 달러 규모 외국인 직접투자 유치 목표", press="Vietnam.vn", domain="vietnam.vn"), "해외 매체"),
    (art("하노이 아파트 가격 급등", press="Laodong.vn", domain="laodong.vn"), "해외 매체"),
    (art("부동산 시장 회복", press="VietnamPlus", domain="example.com"), "해외 매체"),
    (art("VN지수 1,300 돌파"), "해외 지명·지수"),
    (art("베트남 부동산 급등세"), "해외 지명·지수"),
    (art("Calgary Roughnecks win"), "해외 기사"),
    (art("충청남도 기간제근로자 채용 공고", source="local_gov", press="충청남도"), "기사 아닌 페이지"),
])
def test_foreign_and_junk_excluded(cfg, a, expected):
    assert exclusion_reason(a, cfg).startswith(expected)


@pytest.mark.parametrize("title", [
    "베트남 관광객 방한 급증…숙박 수요 회복",            # 국내 연관 단어(방한)
    "충남경제진흥원, 베트남 수출기업 통관 대응 지원",     # 국내 지역(충남)
    "기준금리 동결…주담대 금리 영향은",
])
def test_domestic_articles_kept(cfg, title):
    assert exclusion_reason(art(title), cfg) is None


def test_foreign_business_only_for_business_fields(cfg):
    assert foreign_business("호주 시드니 서부 신공항 개항", "사업형", [], cfg).startswith("해외 개발")
    assert foreign_business("미국 증시 급락에 코스피 출렁", "집객형", [], cfg) is None          # 증시는 해외 소식도 필요
    assert foreign_business("경기경제청, 유럽 첨단기업 투자유치 공략", "사업형", [], cfg) is None  # 국내 투자 유치
    assert foreign_business("삼성 미국 공장 착공", "사업형", [{"sido": "충남", "area": ""}], cfg) is None


# ── 분야 분류 ────────────────────────────────────

def test_classify_title_and_company_combo(cfg):
    scores, hits = classify(art("SK하이닉스, 용인 반도체 클러스터 착공"), cfg)
    assert scores.most_common(1)[0][0] == "corp"
    assert "SK 착공" in hits["corp"]


def test_classify_ignores_lookalike_words(cfg):
    scores, _ = classify(art("공매도 재개에 전세계 증시 변동"), cfg)
    assert "auction" not in scores and "rental" not in scores        # 공매도≠공매, 전세계≠전세


def test_classify_ascii_boundary(cfg):
    scores, _ = classify(art("ICT 기업 실적 발표"), cfg)
    assert "transport" not in scores                                  # 'IC' 가 'ICT' 에 걸리지 않음


def test_classify_falls_back_to_query_field(cfg):
    scores, hits = classify(art("아무 키워드도 없는 제목", field="lodging"), cfg)
    assert scores == {"lodging": 1} and hits == {}


# ── 지역 태그 ────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("포항 철강산단 공장 증설", [("경북", "포항시")]),
    ("분당 재건축 선도지구 지정", [("경기", "성남시")]),
    ("인천 중구 영종 개발", [("인천", "영종구"), ("인천", "제물포구")]),
    ("광주 광산구 산단 착공", [("전남광주", "광산구")]),                      # 2026-07 통합
    ("전라남도 여수 관광객 증가", [("전남광주", "여수시")]),
    ("대구경북특별시 출범 논의 재개", [("대구", ""), ("경북", "")]),           # 통합 예정 이름 → 둘 다
    ("경기 광주 오포 물류센터", [("경기", "광주시")]),
])
def test_region_tags(tagger, text, expected):
    got = [(t["sido"], t["area"]) for t in tagger.tag(text)]
    assert sorted(got) == sorted(expected)


@pytest.mark.parametrize("text", [
    "경기 침체 우려에 코스피 하락",       # 경기 ≠ 경기도
    "규제 강화로 거래 위축",              # 강화 ≠ 강화군
    "용인할 수 없는 사상 최대 적자",       # 용인·사상
    "성수기 숙박 요금 상승",              # 성수 ≠ 성동구
    "중구 일대 상권 회복",                # 중구는 여러 시·도에 있어 시·도 이름 없으면 안 붙임
])
def test_region_not_tagged_for_common_words(tagger, text):
    assert tagger.tag(text) == []


def test_region_full_name_still_works(tagger):
    assert [(t["sido"], t["area"]) for t in tagger.tag("용인시 반도체 산단")] == [("경기", "용인시")]
    assert [(t["sido"], t["area"]) for t in tagger.tag("경기 용인 반도체")] == [("경기", "용인시")]


def test_region_label_and_josa():
    assert region_label([{"sido": "경북", "area": "포항시"}]) == "포항"
    assert region_label([{"sido": "서울", "area": ""}]) == "서울"
    assert fill_place("{지역}으로 옮겨 오면", "서울") == "서울로 옮겨 오면"
    assert fill_place("{지역}으로 옮겨 오면", "포항") == "포항으로 옮겨 오면"
    assert fill_place("{지역}은 장기적으로", "부산") == "부산은 장기적으로"
    assert fill_place("{지역} 모텔호텔 매물", "해당 지역") == "해당 지역 모텔호텔 매물"
    assert fill_place("{지역}에서 공사가", "대구") == "대구에서 공사가"


# ── 진행 단계 ────────────────────────────────────

@pytest.mark.parametrize("text, fields, name, points", [
    ("MOU 체결 1년 만에 공장 착공", ["corp"], "착공", 6),
    ("반도체 공장 투자협약 체결", ["corp"], "계획·MOU", 0),
    ("2028년 착공 목표로 보상 착수", ["region"], "착공 예정", 3),
    ("안동댐 준공 50주년 기념 축제", ["region"], "계획·MOU", 0),
    ("신안산선 개통, 출퇴근 30분 단축", ["transport"], "개통", 10),
    ("광역철도 조기개통 기원", ["transport"], "개통 예정", 5),
    ("GTX-C 예타 통과", ["transport"], "예타 통과", 2),
])
def test_stage_detection(cfg, text, fields, name, points):
    s = detect_stage(text, fields, cfg)
    assert (s["name"], s["points"]) == (name, points)


def test_stage_notices_and_delay(cfg):
    plan = detect_stage("반도체 공장 투자협약 체결", ["corp"], cfg)
    assert plan["cautious"] and "확정된 투자가 아닙니다" in plan["notice"]
    tr = detect_stage("국가철도망 구축계획 반영 확정", ["transport"], cfg)
    assert tr["name"] == "계획 반영" and "개통 시기 미정" in tr["notice"]
    late = detect_stage("인천발 KTX 연말개통 어려울듯", ["transport"], cfg)
    assert late["delayed"] and late["upcoming"]
    assert detect_stage("기준금리 동결", ["rate"], cfg) is None


# ── 같은 사건 묶기 ──────────────────────────────

def test_cluster_same_event_and_split_different(cfg):
    arts = [
        art("한은, 신임 부총재보에 최창호 통화정책국장 임명", hours_ago=5),
        art("한은, 통화정책·시장 담당 부총재보에 최창호 통화정책국장 임명", hours_ago=4),
        art("한은 '통화정책·시장 담당 부총재보'에 최창호 국장", hours_ago=3),
        art("삼성물산, 반포푸르지오 리모델링 수주", hours_ago=2),
        art("삼성물산 반포푸르지오 리모델링 시공사 선정", hours_ago=1),
        art("코스피 2% 급락 마감", hours_ago=1),
    ]
    groups = cluster_articles(arts, cfg.scoring["cluster_similarity"])
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2, 3]


def test_cluster_agency_old_name_is_same(cfg):
    m = agency_map_from(cfg.agencies)
    arts = [art("기재부, 내년 예산안 국회 제출", hours_ago=2), art("재정경제부, 내년 예산안 국회 제출", hours_ago=1)]
    assert len(cluster_articles(arts, cfg.scoring["cluster_similarity"], m)) == 1


def test_cluster_not_across_far_days(cfg):
    arts = [art("코스피 2% 급락 마감", hours_ago=100), art("코스피 2% 급락 마감", hours_ago=1, url="u2")]
    assert len(cluster_articles(arts, cfg.scoring["cluster_similarity"])) == 2


# ── 연결 카테고리 ────────────────────────────────

def conn(cfg, fields, text, stage=None, tags=(), place="", rep_title=None, others=()):
    return bridge.connect(fields, text, stage, list(tags), place, cfg,
                          rep_title=text if rep_title is None else rep_title, other_titles=list(others))


def test_bridge_rate_to_loan(cfg):
    b = conn(cfg, ["rate"], "기준금리 동결 주담대 금리")
    assert (b["rule_id"], b["strength"], b["categories"], b["cta"]) == ("rate_loan", "직접", ["대출 가능·불가 판단"], "loan")


def test_bridge_stock_uses_soft_cta(cfg):
    b = conn(cfg, ["stock"], "코스피 급락 변동성 확대", rep_title="코스피 급락 변동성 확대")
    assert b["rule_id"] == "stock_market" and b["direction"]["id"] == "down" and b["strength"] == "간접" and b["cta"] == "soft"


def test_bridge_fx_tourist_bonus_only_in_tourist_area(cfg):
    jeju = conn(cfg, ["macro"], "환율 상승 외국인 관광객", tags=[{"sido": "제주", "area": "제주시"}], place="제주")
    inland = conn(cfg, ["macro"], "환율 상승 외국인 관광객", tags=[{"sido": "충북", "area": "괴산군"}], place="괴산")
    assert jeju["rule_id"] == "fx_tourism" and "모텔호텔" in jeju["categories"]
    assert jeju["tourist_bonus"] == 3 and inland["tourist_bonus"] == 0


def test_bridge_policy_tax_notice(cfg):
    b = conn(cfg, ["policy"], "양도세 중과 유예 종료")
    assert "정확한 세액은 세무 전문가와 확인하세요." in b["notices"]


def test_bridge_construction_stage_rule_with_region(cfg):
    st = detect_stage("포항 이차전지 공장 착공", ["corp"], cfg)
    b = conn(cfg, ["corp"], "포항 이차전지 공장 착공", stage=st, place="포항")
    assert b["rule_id"] == "corp_construction" and b["categories"] == ["모텔호텔"]
    assert b["sentence"].startswith("포항에서 대규모 공사가") and "포항 모텔호텔 매매·임대" in b["sentence"]


def test_bridge_transport_polarity(cfg):
    bypass = conn(cfg, ["transport"], "진주 우회도로 개통")
    assert bypass["rule_id"] == "transport_bypass" and bypass["polarity"] == "악재" and bypass["cta"] == "listing"
    st = detect_stage("신설역 개통 연기", ["transport"], cfg)
    late = conn(cfg, ["transport"], "신설역 개통 연기", stage=st)
    assert late["rule_id"] == "transport_access" and late["polarity"] == "양면"      # 지연이면 호재로 단정 안 함
    plan = conn(cfg, ["transport"], "국가도로망 종합계획 발표")
    assert plan["rule_id"] == "transport_general" and plan["polarity"] == "양면"


def test_bridge_lodging_regulation_and_platform(cfg):
    share = conn(cfg, ["lodging"], "공유숙박 에어비앤비 합법화 논의")
    fire = conn(cfg, ["lodging"], "숙박업 소방 기준 강화")
    fee = conn(cfg, ["lodging"], "야놀자 수수료 인상")
    assert share["rule_id"] == "lodging_regulation" and share["cta"] == "listing"
    assert fire["rule_id"] == "lodging_regulation" and fire["cta"] == "remodel"
    assert fee["rule_id"] == "platform_fee" and fee["categories"] == ["숙박업 정보", "리모델링 위탁 운영"]


def test_bridge_primary_field_rule_first(cfg):
    b = conn(cfg, ["macro", "rate"], "원-엔화 환율 소폭 하락 한은")
    assert b["rule_id"] == "fx_tourism" and b["cta"] == "soft"          # 주 분야(거시경제) 규칙 먼저
    assert "대출 가능·불가 판단" in b["other_categories"]                  # 부 분야 연결은 '함께 연결'로


def test_bridge_remodel_needs_lodging_or_building_context(cfg):
    apt = conn(cfg, ["etc"], "삼성물산 반포푸르지오 아파트 리모델링 수주")
    motel = conn(cfg, ["etc"], "노후 모텔 리모델링 후 객실 단가 상승")
    assert apt["rule_id"] != "remodel" and motel["rule_id"] == "remodel"


def test_bridge_weak_when_no_rule(cfg):
    b = conn(cfg, ["macro"], "수출 실적 발표")
    assert b["strength"] == "약함" and b["categories"] == ["뉴스"] and b["cta"] == "soft"


# ── 점수 ─────────────────────────────────────────

def issue_for(cfg, **kw):
    base = {"related_count": 5, "latest": (NOW - timedelta(hours=2)).isoformat(), "primary_field": "rate",
            "has_briefing": True, "stage": None, "trend": None, "ad": None, "people": [],
            "bridge": {"strength": "직접", "categories": ["대출 가능·불가 판단"], "tourist_bonus": 0}}
    base.update(kw)
    return base


def test_score_with_reasons(cfg):
    s = score_issue(issue_for(cfg), NOW, cfg)
    labels = {r["label"]: r["points"] for r in s["reasons"]}
    # 관련 5 + 최신(3시간 안) 5 + 대중 관심 3 + 보도자료 1 = 14 / 직접 10
    assert labels["관련 기사 5건"] == 5 and labels["대중 관심 분야"] == 3 and labels["정부 보도자료 포함"] == 1
    assert (s["attract"], s["business"], s["total"], s["stars"]) == (14, 10, 24, 2)


def test_score_trend_stage_ad_people_weak(cfg):
    s = score_issue(issue_for(cfg, trend={"rising": True}, stage={"name": "개통", "points": 10},
                              ad=("strong", ["협찬"]), people=["임명"],
                              bridge={"strength": "약함", "categories": ["뉴스"], "tourist_bonus": 0}), NOW, cfg)
    labels = {r["label"]: r["points"] for r in s["reasons"]}
    assert labels["검색 추이 상승(데이터랩 상대 추이)"] == 4 and labels["진행 단계: 개통"] == 10
    assert labels["광고성 표현(협찬)"] == -10 and labels["인물·인사 기사(임명)"] == -8
    assert labels["연결 약함 → 추천 순위 내림"] == -8 and s["business"] == -8


def test_stars(cfg):
    assert score_issue(issue_for(cfg, related_count=10, stage={"name": "개통", "points": 10}), NOW, cfg)["stars"] == 3
    assert score_issue(issue_for(cfg, related_count=1, primary_field="lodging", has_briefing=False,
                                 latest=(NOW - timedelta(hours=30)).isoformat()), NOW, cfg)["stars"] == 1


# ── 오늘 추천 · 블로그 중복 ───────────────────────

POSTS = [
    {"title": "기준금리 동결, 주담대 금리는 어떻게 될까?", "url": "https://blog.naver.com/yantai7/1",
     "published": (NOW - timedelta(days=2)).isoformat()},
    {"title": "청약 경쟁률 서울 94.58대 1·부산 0.67대 1", "url": "https://blog.naver.com/yantai7/2",
     "published": (NOW - timedelta(days=20)).isoformat()},
]


def test_written_match(cfg):
    hit = written_match({"title": "한은 기준금리 동결…주담대 금리 하락 멈추나", "core_keywords": ["기준금리", "주담대"]}, POSTS, NOW, 7)
    assert hit and hit["url"].endswith("/1")
    old = written_match({"title": "청약 경쟁률 서울 급등", "core_keywords": ["청약"]}, POSTS, NOW, 7)
    assert old is None                                   # 20일 전 글은 7일 기준 밖
    other = written_match({"title": "포항 이차전지 공장 착공", "core_keywords": ["착공"]}, POSTS, NOW, 7)
    assert other is None


def _iss(id_, field, total, strength="직접", written=None, attract=None, people=()):
    return {"id": id_, "primary_field": field, "scores": {"total": total, "attract": total if attract is None else attract},
            "bridge": {"strength": strength}, "written": written, "people": list(people)}


def test_pick_today_slots_backup_and_exclusions(cfg):
    issues = [
        _iss("s1", "stock", 30), _iss("s2", "macro", 20),
        _iss("r1", "rate", 40, written={"title": "이미 씀"}),       # 블로그에 썼음 → 제외
        _iss("l1", "lodging", 25),
        _iss("w1", "region", 50, strength="약함"),                 # 연결 약함 → 제외
        _iss("t1", "transport", 22),
    ]
    picks = pick_today(issues, cfg)
    assert [p["issue_id"] for p in picks] == ["s1", "l1", "t1"]
    only_backup = pick_today([_iss("c1", "commercial", 10)], cfg)
    assert only_backup[0]["issue_id"] == "c1" and only_backup[0]["from_backup"]


# ── 데이터랩 ──────────────────────────────────────

def test_datalab_call_plan_respects_limits(cfg):
    conf = cfg.sources["datalab"]
    targets = [[f"키워드{i}"] for i in range(30)]
    st = {}
    assert len(datalab.plan_calls(targets, st, NOW, conf)) == 4                    # 처음: 상위 20개 → 5개씩 4회
    st = {"last_full": (NOW - timedelta(hours=1)).isoformat(),
          "cache": {datalab.trend_key(t): {} for t in targets[:18]}}
    later = datalab.plan_calls(targets, st, NOW, conf)
    assert len(later) == 1 and later[0] == targets[18:20]                          # 3시간 전: 새 이슈만 최대 1회
    st = {"calls": {"date": NOW.date().isoformat(), "count": conf["daily_call_limit"]}}
    assert datalab.plan_calls(targets, st, NOW, conf) == []                        # 하루 상한


def test_datalab_judge():
    rising = [{"ratio": 10}] * 7 + [{"ratio": 30}] * 3
    flat = [{"ratio": 10}] * 10
    assert datalab.judge(rising)["rising"] is True
    assert datalab.judge(flat)["rising"] is False


def test_datalab_update_with_fake_api(cfg, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    sent = []

    class R:
        def __init__(self, body):
            self.body = body

        def json(self):
            return {"results": [{"data": [{"ratio": 10}] * 7 + [{"ratio": 40}] * 3} for _ in self.body["keywordGroups"]]}

    def fake(url, headers=None, json_body=None, **k):
        sent.append(json_body)
        return R(json_body)

    state = {}
    ctx = Context(cfg, state, NOW, get=fake)
    res = datalab.update(ctx, [["기준금리"], ["GTX"], ["모텔"]])
    assert res["calls"] == 1 and len(sent[0]["keywordGroups"]) == 3
    assert sent[0]["endDate"] == "2026-10-25" and sent[0]["timeUnit"] == "date"
    assert datalab.lookup(state, ["GTX"])["rising"] is True
    assert datalab.update(ctx, [["기준금리"], ["GTX"], ["모텔"]])["calls"] == 0     # 바로 다시: 재사용


# ── 키 없는 상태 전체 흐름 ────────────────────────

def test_build_without_keys(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    arts = [
        art("한은, 기준금리 연 2.50% 동결", hours_ago=2, field="rate"),
        art("한은 기준금리 2.50% 동결 결정", hours_ago=1, field="rate", press="뉴스1"),
        art("호치민 부동산 급등", press="Vietnam.vn", domain="vietnam.vn"),
        art("포항 이차전지 공장 착공…공사 인력 2천명", hours_ago=3, field="corp"),
        art("코스피 급락 변동성 확대", hours_ago=1, field="stock"),
    ]
    out = process.build(arts, POSTS, cfg, {}, NOW)
    assert out["datalab_report"]["status"] == "skipped" and "키 없음" in out["datalab_report"]["message"]
    assert out["stats"]["excluded"] == {"해외 매체": 1}
    titles = {i["title"]: i for i in out["issues"]}
    rate = next(i for i in out["issues"] if i["primary_field"] == "rate")
    assert rate["related_count"] == 2 and rate["written"] is not None              # 블로그에 이틀 전 기준금리 글
    pohang = next(i for i in out["issues"] if "포항" in i["title"])
    assert pohang["regions"] == ["경북 포항시"] and pohang["stage"]["name"] == "착공"
    assert pohang["bridge"]["rule_id"] == "corp_construction"
    assert any(r["label"].startswith("진행 단계") for r in pohang["scores"]["reasons"])
    picked = {p["issue_id"] for p in out["today"]}
    assert rate["id"] not in picked                                                 # 이미 쓴 주제는 추천 제외
    assert len(titles) == 3


# ── 방향 판정 (2026-10-26 실제 기사 제목) ─────────────

from collector.direction import detect as detect_direction


@pytest.mark.parametrize("title, expected", [
    ("원/달러 환율 1.5원 내린 1,323.1원…연중 저점 또 경신", "down"),      # 3단계 추천에 올라왔던 기사
    ("’1,310원대 근접’ 원·달러 환율…명목실효환율 2년 만에 최고", "down"),  # 실효환율 최고 = 원화 강세
    ("원화 가치 상승랠리에 명목실효환율도 2년만에 최고", "down"),
    ("원·달러 환율 또 연저점…반도체발 달러 매도에 1323.1원 마감", "down"),
    ("외국인 팔고 서학개미 사는데 환율은 뚝?‥반도체 '초호황'", "down"),
    ("‘킹달러 가고 강원화’ 환율 1,200원대까지 내려간다고?", "down"),
    ("환율 1,400원 돌파…원화 약세 지속", "up"),
    ("달러 강세에 환율 연고점 경신", "up"),
    ("1561원 찍고 1321원...환율변동 11년來 최대", "unclear"),
    ("HD건설기계, 잘 나가던 실적에 환율이 '찬물'", "unclear"),
])
def test_fx_direction_real_titles(cfg, title, expected):
    assert detect_direction("fx", title, [], cfg)["id"] == expected


@pytest.mark.parametrize("title, expected", [
    ("한국은행, 기준금리 3%로 동결…2연속 인상 후 숨고르기", "hold"),
    ("물가 재상승 직면한 美연준, 금리 '매파적 동결' 유력", "hold"),
    ("여윳돈 늘고 금리 오르자…한풀 꺾인 대출", "up"),
    ("금리 또 오르나 … 대출자들, 11월이 두렵다", "up"),
    ("NH농협은행, 한 달새 세번째 대출금리 인하…전세대출 0.3%P↓", "down"),
    ("연준 금리 인상 전망 후퇴에 금값 4,000달러 돌파", "down"),
])
def test_rate_direction_real_titles(cfg, title, expected):
    assert detect_direction("rate", title, [], cfg)["id"] == expected


@pytest.mark.parametrize("title, expected", [
    ("코스피 5400대로 ‘와르르’…7개월만 최저치", "down"),
    ("코스피, 외인 줄매도에 5500선마저 붕괴…7개월 만에 최저", "down"),
    ("코스피, 반도체 반등 무산에 5500선 내줘", "down"),
    ("코스피 사상 최고치 경신…6000선 돌파", "up"),
    ("코스피 혼조 마감", "unclear"),
])
def test_stock_direction_real_titles(cfg, title, expected):
    assert detect_direction("stock", title, [], cfg)["id"] == expected


@pytest.mark.parametrize("title, expected", [
    ("5대 은행 총수신 39조원 증가…정기예금 금리 인상", "up"),
    ("고금리에 은행은 예금금리 올리는데…저축銀 조달비용 부담에 속앓이", "up"),
    ("시중은행 예금 금리 줄줄이 인하", "down"),
])
def test_deposit_direction_real_titles(cfg, title, expected):
    assert detect_direction("deposit", title, [], cfg)["id"] == expected


def test_fx_bridge_sentence_follows_direction(cfg):
    low = conn(cfg, ["macro"], "원/달러 환율 1.5원 내린 1,323.1원…연중 저점 또 경신")
    assert low["rule_id"] == "fx_tourism" and low["direction"]["label"] == "원화 강세(환율 하락)"
    assert "해외여행" in low["sentence"] and "방한" not in low["sentence"]          # 방향 반대 문장이 나오지 않음
    high = conn(cfg, ["macro"], "환율 1,400원 돌파…원화 약세 지속", tags=[{"sido": "제주", "area": ""}])
    assert "방한" in high["sentence"] and high["tourist_bonus"] == 3
    low_jeju = conn(cfg, ["macro"], "환율 연저점", tags=[{"sido": "제주", "area": ""}])
    assert low_jeju["tourist_bonus"] == 0                                            # 원화 강세면 관광지 가산 없음
    unclear = conn(cfg, ["macro"], "환율변동 11년來 최대")
    assert unclear["direction"]["id"] == "unclear" and unclear["cta"] == "soft"


def test_rate_bridge_by_direction(cfg):
    hold = conn(cfg, ["rate"], "한국은행, 기준금리 3%로 동결")
    up = conn(cfg, ["rate"], "금리 또 오르나 … 대출자들, 11월이 두렵다")
    down = conn(cfg, ["rate"], "대출금리 인하…전세대출 0.3%P↓")
    assert "동결" in hold["sentence"] and hold["cta"] == "loan"
    assert "이자가 늘어납니다" in up["sentence"] and "이자 부담이 줄면서" in down["sentence"]
    unclear = conn(cfg, ["rate"], "기준금리 결정 앞두고 시장 촉각")
    assert unclear["direction"]["id"] == "unclear" and unclear["cta"] == "soft"
    no_rate_word = conn(cfg, ["rate"], "주담대는 '유지' 신용대출은 '조인다'…대출문턱 여전")
    assert no_rate_word["direction"] is None and no_rate_word["cta"] == "loan"     # 금리 기사가 아니면 기본 문장


def test_other_titles_help_when_rep_title_is_unclear(cfg):
    d = detect_direction("stock", "코스피 마감 시황", ["코스피 1.46% 하락", "코스피 7개월 만에 최저"], cfg)
    assert d["id"] == "down"


# ── 건의·요구 단계 ──────────────────────────────

@pytest.mark.parametrize("text, fields", [
    ("충북도, 중부·경부고속도로 확장 범도민 서명운동 12월 말까지 연장", ["transport"]),
    ("경기남부광역철도, 국가철도망 구축계획에 반드시 반영시킬 것", ["transport"]),
    ("시민단체, 공공기관 2차 이전 유치 희망 결의", ["national"]),
    ("국가철도망 구축계획 반영 촉구 건의문 전달", ["transport"]),
])
def test_request_stage(cfg, text, fields):
    s = detect_stage(text, fields, cfg)
    assert s["name"] == "건의·요구" and s["points"] == 0
    assert "아직 정부 계획에 반영되지 않은 지역 요구 단계" in s["notice"]


# ── 오늘 추천 기준 ──────────────────────────────

def test_attract_slot_ranks_by_attract_and_people_excluded(cfg):
    issues = [
        _iss("s_total", "stock", 30, attract=10),          # 총점은 높지만 집객 낮음
        _iss("s_attract", "macro", 22, attract=20, strength="간접"),
        _iss("people", "rate", 50, people=["[프로필]"]),   # 인물 기사 → 추천 제외
        _iss("l1", "lodging", 25),
        _iss("t1", "transport", 22),
    ]
    picks = {p["slot"]: p for p in pick_today(issues, cfg)}
    assert picks["집객형 (주식·경제)"]["issue_id"] == "s_attract" and picks["집객형 (주식·경제)"]["ranked_by"] == "집객 점수"
    assert picks["사업형 (금리·대출·숙박)"]["issue_id"] == "l1"


def test_new_tourist_areas(cfg):
    for place in ["제천", "단양", "가평", "양평", "춘천", "평창", "양양", "태안", "보령", "통영", "거제", "남해", "순천", "목포", "군산"]:
        assert place in cfg.regions["tourist_areas"]
    assert bridge.is_tourist([{"sido": "경남", "area": "통영시"}], cfg)
    assert bridge.is_tourist([{"sido": "충북", "area": "단양군"}], cfg)
