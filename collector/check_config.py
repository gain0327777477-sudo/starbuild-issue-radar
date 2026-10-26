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


def datalab_refreshes(hours: list[int], interval_hours: float) -> int:
    """하루 일정대로 돌 때 정기 조회가 몇 번 되는지 (직전 정기 조회 후 interval 이 지났을 때만)."""
    times = [h for h in sorted(hours)] + [h + 24 for h in sorted(hours)]       # 이틀 치로 돌려 둘째 날만 셈
    last, count = None, 0
    for i, t in enumerate(times):
        if last is None or t - last >= interval_hours:
            last = t
            if i >= len(hours):
                count += 1
    return count


def estimate_calls(cfg) -> dict:
    s = cfg.sources
    hours = s["schedule_kst_hours"]
    runs_per_day = len(hours)

    naver_base = len(cfg.all_keywords()) if s["naver_news"]["enabled"] else 0
    naver_combo = len(cfg.combo_queries()) if s["naver_news"]["enabled"] else 0
    naver_per_run = naver_base + naver_combo           # 아침 첫 실행은 기사 수만 늘고 호출 수는 같음

    briefing_per_run = len(s["korea_briefing"]["departments"]) if s["korea_briefing"]["enabled"] else 0
    general_per_run = google_general_queries(cfg) if s["google_news"]["enabled"] else 0

    dl = s["datalab"]
    if dl["enabled"]:
        refreshes = datalab_refreshes(hours, dl["interval_hours"])
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
        "hours": hours,
        "runs_per_day": runs_per_day,
        "naver_base": naver_base,
        "naver_combo": naver_combo,
        "naver_per_run": naver_per_run,
        "naver_per_day": naver_per_run * runs_per_day,
        "naver_cap": s["naver_news"]["daily_call_limit"],
        "briefing_per_run": briefing_per_run,
        "briefing_per_day": briefing_per_run * runs_per_day,
        "general_per_run": general_per_run,
        "general_per_day": general_per_run * runs_per_day,
        "google_per_run": briefing_per_run + general_per_run,
        "google_per_day": (briefing_per_run + general_per_run) * runs_per_day,
        "google_seconds": round((briefing_per_run + general_per_run) * s["google_news"].get("pause_seconds", 1.5)),
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
    print(f"\n[하루 예상 호출 수] 하루 {e['runs_per_day']}회 (한국 시간 {', '.join(f'{h:02d}' for h in e['hours'])}시 정각) 기준")
    print(f"  네이버 뉴스 검색 : 1회 {e['naver_per_run']}건(키워드 {e['naver_base']} + 대기업 조합 {e['naver_combo']}) "
          f"× {e['runs_per_day']} = {e['naver_per_day']:,}건/일 "
          f"(스스로 건 상한 {e['naver_cap']:,} · 네이버 한도 25,000 → {'한도 이내' if e['naver_per_day'] <= 25000 else '⚠ 한도 초과'})")
    print(f"                     아침 첫 실행은 키워드당 기사 {cfg.sources['naver_news'].get('per_keyword_morning', 100)}건"
          f"(평소 {cfg.sources['naver_news']['per_keyword']}건) — 호출 수는 같음")
    print(f"  구글뉴스 RSS     : 1회 {e['google_per_run']}건(정책브리핑 {e['briefing_per_run']} + 일반·지자체 {e['general_per_run']}, 매번 전체) "
          f"× {e['runs_per_day']} = {e['google_per_day']:,}건/일 (공식 한도 없음 · 1회 약 {e['google_seconds']}초)")
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
