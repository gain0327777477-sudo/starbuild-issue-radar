"""4단계 소재 패킷 테스트 — 실제 파이프라인(process.build)으로 이슈를 만든 뒤 패킷을 확인."""
import copy
import re
from datetime import datetime, timedelta

import pytest

from collector import process
from collector.config import find_phrases, load_config
from collector.models import KST, Article
from collector.packet import build_packet, cta_text, fit

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=KST)


@pytest.fixture(scope="module")
def cfg():
    return load_config(scan_root=None)


def art(title, hours_ago=1, field=None, press="연합뉴스", summary=""):
    return Article(id=title, title=title, url=f"https://www.yna.co.kr/{abs(hash(title))}", press=press, domain="yna.co.kr",
                   source="naver", published=(NOW - timedelta(hours=hours_ago)).isoformat(), summary=summary, query="q",
                   field=field, fetched_at=NOW.isoformat())


def packet_for(cfg, titles, field, monkeypatch=None):
    out = process.build([art(t, hours_ago=i + 1, field=field) for i, t in enumerate(titles)], [], cfg, {}, NOW)
    issue = max(out["issues"], key=lambda i: i["related_count"])
    return issue, build_packet(issue, cfg)


MARKDOWN_LINE = re.compile(r"^\s*(#|\*|-\s|>|```|\|)", re.M)


def test_fx_packet_direction_titles_and_disclaimer(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["원/달러 환율 1.5원 내린 1,323.1원…연중 저점 또 경신",
                                "원·달러 환율 또 연저점…반도체발 달러 매도에 1323.1원 마감"], "macro")
    assert issue["bridge"]["direction"]["id"] == "down"
    assert len(p["titles"]) == 3
    for t in p["titles"]:
        assert len(t) <= cfg.packet["title"]["max_chars"]
        assert t.replace(" ", "")[:10].find("환율") != -1                 # 검색 키워드를 앞쪽에
    assert "환율 하락(원화 강세)" in p["question"]
    assert "방한" not in p["sentence"] and "해외여행" in p["sentence"]
    assert any("투자 권유가 아닙니다" in m for m in p["mandatory"])
    assert p["cta"] == "soft" and p["cta_text"].startswith("이런 경제 흐름이")
    assert p["warnings"] == []


def test_plain_text_has_no_markdown_and_no_article_body(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["한은, 기준금리 연 2.50% 동결", "한은 기준금리 2.50% 동결 결정"], "rate")
    text = p["plain_text"]
    assert not MARKDOWN_LINE.search(text)
    for section in ["■ 제목 후보", "■ 핵심 키워드", "■ 검색 의도", "■ 글 틀", "■ 연결 카테고리", "■ 마무리 상담 안내", "■ 출처"]:
        assert section in text
    assert "1) 첫 문단 — 답 먼저" in text and "7) 확인할 것" in text
    assert 5 <= len(p["keywords"]) <= 7
    assert issue["bridge"]["direction"]["id"] == "hold" and "동결" in p["sentence"]


def test_request_prompt_contains_everything_needed(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["코스피 5400대로 ‘와르르’…7개월만 최저치", "코스피 5500선 무너졌다…7개월 만에 최저"], "stock")
    r = p["request_prompt"]
    for must in ["첫 문단에 질문에 대한 답을 먼저", "소제목은 질문형", "한 글에는 질문 하나만 다룹니다: " + p["question"],
                 "날짜·수치·출처", "전문용어는 처음 나올 때 쉬운 말로", "마크다운 기호", "30자 안팎",
                 "[반드시 넣을 문구]", "투자 권유가 아닙니다", "[쓰면 안 되는 표현]", "보장성 표현: 대출 가능",
                 "개별 종목의 매수·매도 추천", "[마무리 상담 안내]", "[출처]", "연합뉴스, 2026.10.26"]:
        assert must in r, must
    assert "붕괴" not in "".join(p["titles"]) and "와르르" not in "".join(p["titles"][:1])


def test_plan_stage_packet_is_cautious(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["포항시, 이차전지 공장 투자협약 체결", "포항 이차전지 공장 투자협약 맺어"], "corp")
    assert issue["stage"]["name"] == "계획·MOU"
    assert any("확정된 투자가 아닙니다" in m for m in p["mandatory"])
    assert p["cautious"] and "단정 표현" in p["request_prompt"] and "~할 계획이다" in p["request_prompt"]
    assert "포항" in p["question"] and any(t.startswith("포항") or "포항" in t[:8] for t in p["titles"])
    assert p["warnings"] == []


def test_request_stage_and_bad_news_wording(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    _, req = packet_for(cfg, ["충북도, 중부고속도로 확장 범도민 서명운동 연장", "충북 중부고속도로 확장 서명운동 연말까지"], "transport")
    assert any("아직 정부 계획에 반영되지 않은 지역 요구 단계" in m for m in req["mandatory"])
    issue, bad = packet_for(cfg, ["진주시 우회도로 개통…도심 통과 차량 줄어", "진주 우회도로 개통, 도심 차량 분산"], "transport")
    assert issue["bridge"]["polarity"] == "악재"
    assert "불안을 키우는 표현 대신 '확인할 것' 중심" in bad["request_prompt"]
    assert any("통행량" in c for c in bad["checklist"])


def test_policy_tax_packet_has_tax_notice(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    _, p = packet_for(cfg, ["양도세 중과 유예 내년 종료…다주택자 셈법 복잡", "양도세 중과 유예 종료 앞두고 매물 증가"], "policy")
    assert "정확한 세액은 세무 전문가와 확인하세요." in p["mandatory"]


def test_office_info_only_from_office_yaml(cfg):
    c = copy.deepcopy(cfg)
    body, footer = cta_text("listing", c)
    assert body.startswith("전국 모텔·호텔 매매 전문 스타빌드입니다.")
    assert footer == ""                                      # 지금은 office.yaml 이 비어 있어 줄이 모두 빠짐
    c.office.update(name="스타빌드공인중개사사무소", registration_no="제0000-00000호", address="서울시 어딘가 1",
                    phone="02-000-0000", mobile="", kakao="")
    _, footer = cta_text("listing", c)
    assert footer.splitlines() == ["스타빌드공인중개사사무소", "중개사무소 등록번호 제0000-00000호",
                                   "소재지 서울시 어딘가 1", "전화 02-000-0000"]     # 빈 휴대폰·카카오 조각은 빠짐


def test_find_phrases_does_not_join_words():
    assert find_phrases("대출가능 여부", ["대출 가능"]) == ["대출 가능"]
    assert find_phrases("공장 착공 포항 지역", ["공포"]) == []            # '착공 포항' ≠ 공포
    assert find_phrases("확정된 투자가 아닙니다", ["확정"], ["확정된 투자가 아닙니다"]) == []


def test_fit_cuts_at_space():
    assert fit("가나다 라마바 사아자 차카타", 10) == "가나다 라마바"
    assert fit("짧은 제목", 32) == "짧은 제목"


def test_all_real_packets_pass_checks(cfg):
    """지금 data/ 에 있는 실제 이슈로 만든 패킷 전체에 금지 표현 경고가 없어야 함 (데이터가 있을 때만)."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "data" / "issues.json"
    if not f.exists():
        pytest.skip("data/issues.json 없음 (수집기를 한 번 실행하면 확인)")
    issues = json.loads(f.read_text(encoding="utf-8"))["issues"]
    bad = [(i["title"], w) for i in issues for w in build_packet(i, cfg)["warnings"]]
    assert bad == []
    assert all(len(build_packet(i, cfg)["titles"]) == 3 for i in issues[:100])


# ── 4단계 보완 (2026-10-26 검토) ───────────────────

from collector import bridge
from collector.filters import people_words
from collector.packet import clean_headline


@pytest.mark.parametrize("title, excluded", [
    ("[프로필] 최창호 한국은행 통화정책·시장 담당 부총재보", True),
    ("신임 국토부 장관 취임…공급 대책 속도 낸다", False),        # 정책 단어(공급·대책) → 정책 기사
    ("금융위원장 내정자 \"가계대출 규제 유지\"", False),           # 대출·규제
    ("[부고] 홍길동씨 별세", True),
    ("전 한은 총재 별세…금리 정책 이끈 인물", True),              # 별세·부고·동정은 정책 단어가 있어도 제외
])
def test_people_exception_for_policy_words(cfg, title, excluded):
    assert bool(people_words(title, cfg)) is excluded


def test_single_stock_issue_uses_money_flow_angle(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["SK하이닉스 키옥시아 차익 16조원 특별배당, 확정된 바 없어",
                                "SK하이닉스 \"16조 특별배당 미확정\"…28일 주주환원 방안 발표"], "stock")
    br = issue["bridge"]
    assert br["rule_id"] == "single_stock" and br["cta"] == "soft"
    assert br["single_stock"] == {"company": "SK하이닉스", "event": "특별배당"}
    assert p["question"] == "SK하이닉스 특별배당 이후, 이 돈은 어디로 움직일까?"
    assert p["titles"][0] == "SK하이닉스 특별배당, 목돈은 어디로 움직일까?"
    assert "주가·실적 전망은 쓰지 말고, 이 돈이 어디로 움직일 수 있는지와 수익형 부동산 관점만 쓸 것" in p["request_prompt"]
    assert any("투자 권유가 아닙니다" in m for m in p["mandatory"])
    assert p["cta_text"].startswith("이런 경제 흐름이")


def test_market_issue_without_company_is_not_single_stock(cfg):
    assert bridge.single_stock_match("코스피 5500선 붕괴…7개월 만에 최저", ["stock"], cfg) is None
    assert bridge.single_stock_match("삼성전자 배당 확대", ["rate"], cfg) is None      # 증시·경제 분야가 아니면 아님


def test_titles_are_not_keyword_plus_original_headline(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    issue, p = packet_for(cfg, ["충북도, 중부·경부선 고속도로 확장 서명운동 12월 말까지 연장",
                                "충북도 고속도로 확장 서명운동 연말까지 연장"], "transport")
    gist = clean_headline(issue["title"])
    assert all(gist not in t for t in p["titles"])
    kinds = ["어떤 영향" in p["titles"][0] or "?" in p["titles"][0], "정리" in p["titles"][1] or "쉽게" in p["titles"][1],
             "확인할" in p["titles"][2]]
    assert all(kinds), p["titles"]                                                   # 질문형·설명형·확인형


def test_no_doubled_action_words_in_titles(cfg, monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    _, p = packet_for(cfg, ["아산 배방 진입도로 개설 개통…주민 편의 개선", "아산 배방 진입도로 개설 완료 개통"], "transport")
    assert not any("개설 개통" in t for t in p["titles"]), p["titles"]
