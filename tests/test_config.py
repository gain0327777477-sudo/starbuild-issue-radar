import shutil

import pytest

from collector import env
from collector.check_config import estimate_calls
from collector.config import CONFIG_DIR, ConfigError, load_config, parse_keyword


def test_real_config_loads():
    cfg = load_config()
    assert "stock" in cfg.fields
    assert "대출 가능·불가 판단" in cfg.category_names
    assert len(cfg.all_keywords()) > 50


def test_keyword_synonyms():
    kw = parse_keyword("리츠|REITs")
    assert kw.query == "리츠"
    assert kw.terms == ["리츠", "REITs"]


def _copy_config(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    return d


def test_bridge_with_unknown_category_is_reported(tmp_path):
    d = _copy_config(tmp_path)
    p = d / "bridges.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("[대출 가능·불가 판단]", "[대출판단]", 1), encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(d)
    assert any("대출판단" in msg and "blog_categories.yaml" in msg for msg in e.value.problems)


def test_broken_yaml_reports_file_and_line(tmp_path):
    d = _copy_config(tmp_path)
    (d / "scoring.yaml").write_text("attract:\n  stars: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(d)
    assert any(msg.startswith("scoring.yaml") and "줄" in msg for msg in e.value.problems)


def test_empty_office_is_only_a_warning():
    cfg = load_config()
    assert any("office.yaml" in w for w in cfg.warnings)


def test_datalab_estimate_stays_under_limit():
    e = estimate_calls(load_config())
    assert e["datalab"]["worst"] <= 1000
    assert e["naver_per_day"] <= 25000


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
