"""수집 일정(한국 06~22시 2시간마다) — cron 변환, 예약 시각, 야간, 호출 수."""
import shutil
from datetime import datetime

import pytest

from collector.check_config import estimate_calls
from collector.config import CONFIG_DIR, load_config
from collector.models import KST
from collector.schedule import cron_kst_hours, is_quiet, kst_to_utc_hours, next_run, scheduled_slot, workflow_cron

HOURS = [6, 8, 10, 12, 14, 16, 18, 20, 22]
T = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=KST)


def test_kst_to_utc_table():
    # 한국 06=UTC21(전날) 08=23 10=01 12=03 14=05 16=07 18=09 20=11 22=13
    assert kst_to_utc_hours(HOURS) == [1, 3, 5, 7, 9, 11, 13, 21, 23]
    assert cron_kst_hours("0 1,3,5,7,9,11,13,21,23 * * *") == HOURS
    assert cron_kst_hours("*/30 * * * *") is None


def test_workflow_cron_matches_sources_yaml():
    assert cron_kst_hours(workflow_cron()) == load_config(scan_root=None).sources["schedule_kst_hours"] == HOURS


def test_cron_mismatch_is_warned(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    p = d / "sources.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("[6, 8, 10, 12, 14, 16, 18, 20, 22]", "[7, 9, 11]"), encoding="utf-8")
    cfg = load_config(d, scan_root=None)
    assert any("수집 일정 불일치" in w and "'0 0,2,22 * * *'" in w for w in cfg.warnings)


@pytest.mark.parametrize("t, slot", [
    (T(27, 6, 12), T(27, 6)), (T(27, 7, 59), T(27, 6)), (T(27, 22, 30), T(27, 22)),
    (T(27, 3, 0), T(26, 22)),             # 새벽은 전날 22시 예약 다음
])
def test_scheduled_slot(t, slot):
    assert scheduled_slot(t, HOURS) == slot


def test_next_run_and_quiet():
    assert next_run(T(27, 9, 5), HOURS) == T(27, 10)
    assert next_run(T(27, 22, 5), HOURS) == T(28, 6)
    q = {"start": 22, "end": 7}
    assert [is_quiet(T(27, h), q) for h in (21, 22, 23, 0, 6, 7)] == [False, True, True, True, True, False]


def test_daily_calls_for_new_schedule():
    e = estimate_calls(load_config(scan_root=None))
    assert e["runs_per_day"] == 9
    assert e["naver_per_day"] == e["naver_per_run"] * 9 <= 25000
    assert e["google_per_day"] == (e["briefing_per_run"] + e["general_per_run"]) * 9
    assert e["datalab"]["refreshes"] == 9 and e["datalab"]["worst"] == 36
