import shutil

import pytest

from collector import env
from collector.check_config import estimate_calls
from collector.config import CONFIG_DIR, ConfigError, find_phrases, load_config, parse_keyword, scan_hardcoded_office_info


def test_real_config_loads_without_text_warnings():
    cfg = load_config()
    assert {"stock", "corp", "national", "transport"} <= set(cfg.fields)
    assert "대출 가능·불가 판단" in cfg.category_names
    assert len(cfg.regions["regions"]) == 16          # 2026-07 전남광주통합특별시 출범으로 17 → 16
    assert "전라남도" in cfg.regions["regions"]["전남광주"]["names"]
    assert cfg.regions["combined_aliases"]["대구경북특별시"] == ["대구", "경북"]
    assert "soft" in cfg.cta_templates and cfg.soft_cta_fields == ["stock", "macro", "asset"]
    assert cfg.cta_templates["listing"].startswith("전국 모텔·호텔 매매 전문 {short_name}입니다.")
    bad = [w for w in cfg.warnings if "보장성" in w or "공포" in w or "단정" in w or "office.yaml 밖" in w]
    assert bad == []


def test_keyword_synonyms():
    kw = parse_keyword("리츠|REITs")
    assert kw.query == "리츠"
    assert kw.terms == ["리츠", "REITs"]


def test_agency_old_names_grouped():
    cfg = load_config()
    assert cfg.agency_terms("재정경제부")[:2] == ["재정경제부", "기획재정부"]
    assert "기획재정부" in cfg.agency_terms("기획예산처")
    assert cfg.agency_terms("국세청") == ["국세청"]


def test_company_combos():
    cfg = load_config()
    combos = cfg.combo_queries()
    assert ("corp", "삼성 착공") in combos
    assert len(combos) == len(cfg.company_combos["companies"]) * len(cfg.company_combos["actions"])


def test_blog_rss_comes_from_office_yaml():
    assert load_config().blog_rss_url == "https://rss.blog.naver.com/yantai7.xml"


def _copy_config(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    return d


def _replace(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_bridge_with_unknown_category_is_reported(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "[대출 가능·불가 판단]", "[대출판단]")
    with pytest.raises(ConfigError) as e:
        load_config(d, scan_root=None)
    assert any("대출판단" in msg and "blog_categories.yaml" in msg for msg in e.value.problems)


def test_transport_rule_needs_polarity(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "    polarity: 악재\n", "")
    with pytest.raises(ConfigError) as e:
        load_config(d, scan_root=None)
    assert any("transport_bypass" in msg and "polarity" in msg for msg in e.value.problems)


def test_unknown_stage_in_rule_is_reported(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "stages: [done]\n    categories: [모텔호텔, 상가, 건물]", "stages: [opening]\n    categories: [모텔호텔, 상가, 건물]")
    with pytest.raises(ConfigError) as e:
        load_config(d, scan_root=None)
    assert any("opening" in msg for msg in e.value.problems)


def test_unknown_placeholder_in_cta_is_reported(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "매매 전문 {short_name}입니다", "매매 전문 {상호}입니다")
    with pytest.raises(ConfigError) as e:
        load_config(d, scan_root=None)
    assert any("{상호}" in msg and "office.yaml" in msg for msg in e.value.problems)


def test_broken_yaml_reports_file_and_line(tmp_path):
    d = _copy_config(tmp_path)
    (d / "scoring.yaml").write_text("attract:\n  stars: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(d, scan_root=None)
    assert any(msg.startswith("scoring.yaml") and "줄" in msg for msg in e.value.problems)


def test_guarantee_phrase_in_cta_is_warned(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "편하게 문의 주세요. 매물과", "대출가능 여부를 승인 보장 수준으로 알려드립니다. 매물과")
    cfg = load_config(d, scan_root=None)
    w = [x for x in cfg.warnings if "보장성" in x]
    assert w and "대출 가능" in w[0] and "승인 보장" in w[0]


def test_category_name_itself_is_not_a_guarantee_phrase():
    assert find_phrases("대출 가능·불가 판단", ["대출 가능"]) == ["대출 가능"]   # 그냥 찾으면 걸리지만
    cfg = load_config()                                                         # 카테고리 이름은 검사에서 뺌
    assert not any("rate_loan" in w for w in cfg.warnings)


def test_fear_phrase_is_warned(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "흐름이 바뀔 수 있습니다. 도로변", "흐름이 바뀌어 매출 직격탄입니다. 도로변")
    cfg = load_config(d, scan_root=None)
    assert any("공포" in x and "직격탄" in x for x in cfg.warnings)


def test_assertive_words_in_plan_stage_sentence_warned(tmp_path):
    d = _copy_config(tmp_path)
    _replace(d / "bridges.yaml", "근로자와 출장 수요가 늘 수 있습니다", "근로자와 출장 수요가 반드시 생긴다")
    cfg = load_config(d, scan_root=None)
    assert any("corp_general" in x and "단정" in x for x in cfg.warnings)


def test_hardcoded_phone_outside_office_yaml(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "bridges.yaml").write_text("cta: 문의 010-1234-5678\n", encoding="utf-8")
    (tmp_path / "config" / "office.yaml").write_text('phone: "010-1234-5678"\n', encoding="utf-8")
    (tmp_path / "collector").mkdir()
    (tmp_path / "collector" / "x.py").write_text('REG = "중개사무소 등록번호 41135-2019-00123"\n', encoding="utf-8")
    spots = scan_hardcoded_office_info(tmp_path, {"phone": "010-1234-5678"})
    assert spots == ["config/bridges.yaml 1번째 줄", "collector/x.py 1번째 줄"]


def test_empty_office_is_only_a_warning():
    cfg = load_config()
    assert any("office.yaml" in w and "비어" in w for w in cfg.warnings)


def test_call_estimates_stay_under_limits():
    e = estimate_calls(load_config())
    assert e["naver_per_day"] <= 25000
    assert e["datalab"]["worst"] <= 1000
    assert e["briefing_per_day"] == len(load_config().sources["korea_briefing"]["departments"]) * 48


def test_dotenv_does_not_override_and_mask(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "from-github-secret")
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    f = tmp_path / ".env"
    f.write_text("# 주석\nNAVER_CLIENT_ID=from-file\nNAVER_CLIENT_SECRET=\"abcd1234\"\n", encoding="utf-8")
    loaded = env.load_dotenv(f)
    assert loaded == ["NAVER_CLIENT_SECRET"]
    assert env.get_secret("NAVER_CLIENT_ID") == "from-github-secret"
    assert env.mask("오류: secret=abcd1234 응답") == "오류: secret=ab**** 응답"


def test_no_keys(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "   ")
    assert env.has_naver_keys() is False
