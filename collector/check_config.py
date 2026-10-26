"""설정 파일 검사 + 하루 예상 호출 수 계산.

실행:  python -m collector.check_config
"""
from __future__ import annotations

import math
import sys

from .config import ConfigError, load_config
from .env import ROOT_ENV, has_naver_keys, load_dotenv


def estimate_calls(cfg) -> dict:
    s = cfg.sources
    runs_per_day = (24 * 60) // s["collect_every_minutes"]

    naver_per_run = len(cfg.all_keywords()) if s["naver_news"]["enabled"] else 0

    kpq = s["google_news"]["keywords_per_query"]
    google_per_run = sum(math.ceil(len(f.keywords) / kpq) for f in cfg.fields.values()) if s["google_news"]["enabled"] else 0
    briefing_per_run = len(s["korea_briefing"]["departments"]) if s["korea_briefing"]["enabled"] else 0

    dl = s["datalab"]
    if dl["enabled"]:
        refreshes = math.ceil(24 / dl["interval_hours"])
        per_refresh = math.ceil(dl["top_issues"] / dl["groups_per_call"])
        extra_worst = (runs_per_day - refreshes) * dl["max_new_calls_per_run"]
        datalab = {
            "refreshes": refreshes,
            "per_refresh": per_refresh,
            "base": refreshes * per_refresh,
            "extra_worst": extra_worst,
            "worst": min(refreshes * per_refresh + extra_worst, dl["daily_call_limit"]),
            "cap": dl["daily_call_limit"],
        }
    else:
        datalab = None

    return {
        "runs_per_day": runs_per_day,
        "naver_per_run": naver_per_run,
        "naver_per_day": naver_per_run * runs_per_day,
        "naver_cap": s["naver_news"]["daily_call_limit"],
        "google_per_run": google_per_run + briefing_per_run,
        "google_per_day": (google_per_run + briefing_per_run) * runs_per_day,
        "datalab": datalab,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    load_dotenv(ROOT_ENV)
    try:
        cfg = load_config()
    except ConfigError as e:
        print("❌", e)
        return 1

    print("✅ 설정 파일 7개를 모두 정상적으로 읽었습니다.\n")
    print(f"  분야 {len(cfg.fields)}개 / 검색 키워드 {len(cfg.all_keywords())}개 / "
          f"연결 규칙 {len(cfg.rules)}개 / 블로그 카테고리 {len(cfg.categories)}개")
    print(f"  네이버 키: {'있음' if has_naver_keys() else '없음 → RSS(구글뉴스·정책브리핑·블로그)만으로 수집'}")

    e = estimate_calls(cfg)
    print(f"\n[하루 예상 호출 수] 수집 {e['runs_per_day']}회/일 (30분 간격) 기준")
    print(f"  네이버 뉴스 검색 : 1회 {e['naver_per_run']}건 × {e['runs_per_day']} = {e['naver_per_day']:,}건/일 "
          f"(스스로 건 상한 {e['naver_cap']:,} · 네이버 한도 25,000)")
    print(f"  구글뉴스 RSS     : 1회 {e['google_per_run']}건 × {e['runs_per_day']} = {e['google_per_day']:,}건/일 (키·한도 없음)")
    d = e["datalab"]
    if d:
        print(f"  데이터랩 트렌드  : 정기 {d['refreshes']}회 × {d['per_refresh']}건 = {d['base']}건/일, "
              f"새 상위 이슈 추가 조회 최대 {d['extra_worst']}건 → 최악 {d['worst']}건/일 "
              f"(스스로 건 상한 {d['cap']} · 네이버 한도 1,000)")

    if cfg.warnings:
        print("\n[참고]")
        for w in cfg.warnings:
            print("  ⚠", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
