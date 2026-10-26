"""6단계: 배포용 사이트 묶기 + 실행 정보."""
import json
from datetime import datetime

import yaml

from collector import main as collector_main
from collector.models import KST
from tools import build_site


def test_build_site_injects_version_and_skips_private_files(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "packets").mkdir(parents=True)
    for name in ["status.json", "issues.json", "today.json", "articles.json", "state.json", "blog_posts.json"]:
        (data / name).write_text("{}", encoding="utf-8")
    (data / "packets" / "abc.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build_site, "DATA", data)

    out = tmp_path / "_site"
    build_site.build(out, "abc1234-77")
    assert 'const VERSION = "abc1234-77";' in (out / "sw.js").read_text(encoding="utf-8")
    assert json.loads((out / "version.json").read_text(encoding="utf-8"))["version"] == "abc1234-77"
    assert sorted(p.name for p in (out / "data").iterdir()) == ["issues.json", "packets", "status.json", "today.json"]
    assert (out / "data" / "packets" / "abc.json").exists() and (out / ".nojekyll").exists()
    assert (out / "index.html").exists() and (out / "manifest.webmanifest").exists()


def test_run_info_for_scheduled_run(monkeypatch):
    monkeypatch.setenv("RADAR_RUN_EVENT", "schedule")
    monkeypatch.setenv("RADAR_RUN_ID", "123")
    info = collector_main.run_info(datetime(2026, 10, 27, 9, 47, 30, tzinfo=KST))
    assert info["scheduled_slot"] == "2026-10-27T09:30:00+09:00" and info["start_delay_min"] == 17.5
    monkeypatch.setenv("RADAR_RUN_EVENT", "workflow_dispatch")
    assert "scheduled_slot" not in collector_main.run_info(datetime(2026, 10, 27, 9, 47, tzinfo=KST))


def test_workflow_has_required_pieces():
    w = yaml.safe_load(open(build_site.ROOT / ".github" / "workflows" / "collect.yml", encoding="utf-8"))
    on = w[True]                                     # YAML 의 'on' 은 True 로 읽힘
    assert on["schedule"][0]["cron"] == "*/30 * * * *" and "workflow_dispatch" in on
    assert w["permissions"]["actions"] == "write" and w["permissions"]["pages"] == "write"
    steps = {s["name"]: s for s in w["jobs"]["run"]["steps"]}
    assert "secrets.NAVER_CLIENT_SECRET" in steps["수집·점수·소재 패킷"]["env"]["NAVER_CLIENT_SECRET"]
    assert "gh workflow enable" in steps["60일 자동 중지 대비 (워크플로 켜기)"]["run"]
    assert "--force" in steps["수집 결과를 data 브랜치에 덮어쓰기"]["run"]
