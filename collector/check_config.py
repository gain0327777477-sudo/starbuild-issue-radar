"""설정 파일 검사 + 하루 예상 호출 수 계산.

실행:  python -m collector.check_config
"""
from __future__ import annotations

import math
import sys

from .config import FILES, ConfigError, load_config
from .env import ROOT_ENV, has_naver_keys, load_dotenv


def google_general_queries(cfg) -> int:
    """구글뉴스 일반 검색 한 바퀴에 필요한 검색 수 (분야별 OR 묶음 + 지자체 사이트)."""
    gn = cfg.sources["google_news"]
    kpq = gn["keywords_per_query"]
    per_field = sum(math.ceil(len(f.keywords) / kpq) for f in cfg.fields.values())
    local = len((gn.get("local_gov") or {}).get("sites") or [])
    return per_field + local


def estimate_calls(cfg) -> dict:
    s = cfg.sources
    every = s["collect_every_minutes"]
    runs_per_day = (24 * 60) // every

    naver_base = len(cfg.all_keywords()) if s["naver_news"]["enabled"] else 0
    naver_combo = len(cfg.combo_queries()) if s["naver_news"]["enabled"] else 0
    naver_per_run = naver_base + naver_combo

    briefing_per_run = len(s["korea_briefing"]["departments"]) if s["korea_briefing"]["enabled"] else 0
    gn = s["google_news"]
    if gn["enabled"]:
        general_cycle = google_general_queries(cfg)
        cycles_per_day = 24 / gn["interval_hours"]
        runs_per_cycle = max(1, round(gn["interval_hours"] * 60 / every))
        general_per_day = math.ceil(general_cycle * cycles_per_day)
        general_per_run = math.ceil(general_cycle / runs_per_cycle)
    else:
        general_cycle = general_per_day = general_per_run = runs_per_cycle = 0

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
        "naver_base": naver_base,
        "naver_combo": naver_combo,
        "naver_per_run": naver_per_run,
        "naver_per_day": naver_per_run * runs_per_day,
        "naver_cap": s["naver_news"]["daily_call_limit"],
        "briefing_per_run": briefing_per_run,
        "briefing_per_day": briefing_per_run * runs_per_day,
        "general_cycle": general_cycle,
        "general_per_run": general_per_run,
        "general_per_day": general_per_day,
        "runs_per_cycle": runs_per_cycle,
        "google_per_day": briefing_per_run * runs_per_day + general_per_day,
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

    print(f"✅ 설정 파일 {len(FILES)}개를 모두 정상적으로 읽었습니다.\n")
    print(f"  분야 {len(cfg.fields)}개 / 검색 키워드 {len(cfg.all_keywords())}개 + 대기업 조합 {len(cfg.combo_queries())}개 / "
          f"연결 규칙 {len(cfg.rules)}개 / 블로그 카테고리 {len(cfg.categories)}개 / 시·도 {len(cfg.regions['regions'])}개")
    print(f"  네이버 키: {'있음' if has_naver_keys() else '없음 → RSS(구글뉴스·정책브리핑·블로그)만으로 수집'}")
    print(f"  블로그 RSS: {cfg.blog_rss_url}")

    e = estimate_calls(cfg)
    print(f"\n[하루 예상 호출 수] 수집 {e['runs_per_day']}회/일 ({cfg.sources['collect_every_minutes']}분 간격) 기준")
    print(f"  네이버 뉴스 검색 : 1회 {e['naver_per_run']}건(키워드 {e['naver_base']} + 대기업 조합 {e['naver_combo']}) "
          f"× {e['runs_per_day']} = {e['naver_per_day']:,}건/일 "
          f"(스스로 건 상한 {e['naver_cap']:,} · 네이버 한도 25,000 → {'한도 이내' if e['naver_per_day'] <= 25000 else '⚠ 한도 초과'})")
    print(f"  구글뉴스 RSS     : 정책브리핑 {e['briefing_per_run']}건 × {e['runs_per_day']} = {e['briefing_per_day']:,}건/일 (30분마다)")
    print(f"                     일반 검색 {e['general_cycle']}건을 {cfg.sources['google_news']['interval_hours']}시간에 한 바퀴"
          f"(30분마다 약 {e['general_per_run']}건씩 나눠서) = {e['general_per_day']:,}건/일")
    print(f"                     합계 {e['google_per_day']:,}건/일 (공식 한도 없음)")
    d = e["datalab"]
    if d:
        print(f"  데이터랩 트렌드  : 정기 {d['refreshes']}회 × {d['per_refresh']}건 = {d['base']}건/일, "
              f"새 상위 이슈 추가 조회 최대 {d['extra_worst']}건 → 최악 {d['worst']}건/일 "
              f"(스스로 건 상한 {d['cap']} · 네이버 한도 1,000)")

    if cfg.warnings:
        print("\n[확인 필요]")
        for w in cfg.warnings:
            print("  ⚠", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
