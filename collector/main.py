"""수집기 실행 시작점.

실행:  python -m collector.main
결과:  data/articles.json (최근 7일 기사), data/blog_posts.json, data/status.json(화면 상단 표시용), data/state.json(내부 기록)
       data/issues.json (화면용 이슈), data/today.json (오늘 쓸 글 추천 3개) — 3단계(process.py)

소스 하나가 실패해도 나머지는 계속 수집하고, 실패 내역은 status.json 에 남겨 화면에 표시한다.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

from . import process
from .config import ConfigError, load_config
from .env import ROOT_ENV, has_naver_keys, load_dotenv, mask
from .http import SourceError
from .models import Article, now_kst
from .schedule import next_run, scheduled_slot
from .sources import blog_rss, google_news, korea_briefing, naver_news
from .sources.base import Context, SkipSource
from .press import fix_press, learn
from .store import DATA_DIR, load_articles, load_json, merge_articles, save_articles, save_json

SOURCES = (naver_news, korea_briefing, google_news, blog_rss)


def run_source(module, ctx: Context) -> tuple[dict, object]:
    """소스 하나 실행. 어떤 오류가 나도 여기서 잡아서 보고서로 바꾼다."""
    started = time.monotonic()
    report = {"id": module.ID, "name": module.NAME, "status": "ok", "count": 0, "calls": 0, "message": ""}
    result = None
    try:
        result = module.collect(ctx)
        report["count"] = len(result.articles) or len(result.extra.get("posts", []))
        report["calls"] = result.calls
        report["message"] = " / ".join(result.notes)
        if any(w in report["message"] for w in ("실패", "막음", "중단")):
            report["status"] = "partial"
    except SkipSource as e:
        report.update(status="skipped", message=str(e))
    except SourceError as e:
        report.update(status="failed", message=str(e))
    except Exception as e:  # 예상 못 한 오류도 다른 소스를 멈추지 않게
        report.update(status="failed", message=mask(f"프로그램 오류: {type(e).__name__}: {e}"))
        traceback.print_exc()
    report["seconds"] = round(time.monotonic() - started, 1)
    last_ok = ctx.state.setdefault("last_success", {})
    if report["status"] in ("ok", "partial"):
        last_ok[module.ID] = ctx.now.isoformat()
    report["last_success"] = last_ok.get(module.ID, "")
    return report, result


def run_info(started, cfg=None) -> dict:
    """어떻게 실행됐는지 — 반영 지연(예약 시각 → 화면) 측정용.
    예약 실행(schedule)이면 예약 시각 = 일정표(schedule_kst_hours)에서 시작 시각 직전의 정각."""
    event = os.environ.get("RADAR_RUN_EVENT", "local")
    info = {"event": event, "run_id": os.environ.get("RADAR_RUN_ID", ""), "started_at": started.isoformat(),
            "finished_at": now_kst().isoformat()}
    if event == "schedule":
        hours = cfg.sources["schedule_kst_hours"] if cfg else list(range(24))
        slot = scheduled_slot(started, hours)
        info["scheduled_slot"] = slot.isoformat()
        info["start_delay_min"] = round((started - slot).total_seconds() / 60, 1)
    return info


def schedule_info(cfg, now) -> dict:
    """화면이 쓰는 일정 정보: 다음 수집, 야간 휴식 시간, 경고 기준."""
    s = cfg.sources
    return {"kst_hours": s["schedule_kst_hours"], "quiet_hours": s["quiet_hours"], "stale_hours": s["stale_hours"],
            "next_run": next_run(now, s["schedule_kst_hours"]).isoformat()}


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

    now = now_kst()
    state = load_json(DATA_DIR / "state.json", {})
    ctx = Context(cfg=cfg, state=state, now=now)
    print(f"수집 시작 {now:%Y-%m-%d %H:%M} (네이버 키 {'있음' if has_naver_keys() else '없음'})")

    reports, new_articles, posts = [], [], None
    for module in SOURCES:
        report, result = run_source(module, ctx)
        reports.append(report)
        if result is not None:
            new_articles += result.articles
            posts = result.extra.get("posts", posts)
        mark = {"ok": "✅", "partial": "⚠", "skipped": "⏭", "failed": "❌"}[report["status"]]
        print(f"  {mark} {report['name']}: {report['count']}건, 요청 {report['calls']}회, {report['seconds']}초"
              + (f" — {report['message']}" if report["message"] else ""))

    # 언론사 이름: 구글 뉴스에서 '도메인 → 이름'을 배우고, 도메인으로 남은 기사(옛 기사 포함)를 고침
    learned = state.setdefault("press_learned", {})
    new_names = learn(new_articles, learned)
    old = load_articles(DATA_DIR)
    articles = merge_articles(old, new_articles, now, cfg.scoring.get("retention_days", 7))
    fixed = fix_press(articles, cfg.press, learned)
    if new_names or fixed:
        print(f"언론사 이름: 새로 배운 도메인 {new_names}개, 이름을 고친 기사 {fixed}건")
    sizes = save_articles(DATA_DIR, articles, now)
    if posts is not None:
        save_json(DATA_DIR / "blog_posts.json", {"updated_at": now.isoformat(), "posts": posts})
    print(f"새로 받은 기사 {len(new_articles)}건 → 중복 정리 후 보관 중인 기사 {len(articles)}건 (최근 {cfg.scoring.get('retention_days', 7)}일, "
          f"날짜별 파일 {len(sizes)}개 · 합계 {sum(sizes.values()) / 1024 / 1024:.1f}MB · 가장 큰 파일 {max(sizes.values(), default=0) / 1024 / 1024:.1f}MB)")

    # 3단계: 정제·묶기·연결·점수·추천 (데이터랩은 상위 이슈가 정해진 뒤 여기서 조회)
    stats = None
    try:
        out = process.run(cfg, state, now, DATA_DIR, get=ctx.get)
        reports.append(out["datalab_report"])
        stats = out["stats"]
        process.print_summary(out)
    except Exception as e:
        reports.append({"id": "process", "name": "정제·점수 계산", "status": "failed", "count": 0, "calls": 0,
                        "message": mask(f"프로그램 오류: {type(e).__name__}: {e}"), "seconds": 0, "last_success": ""})
        traceback.print_exc()

    failed = [r for r in reports if r["status"] in ("failed", "partial")]
    save_json(DATA_DIR / "status.json", {
        "collected_at": now.isoformat(),
        "run": run_info(now, cfg),
        "schedule": schedule_info(cfg, now),
        "has_naver_keys": has_naver_keys(),
        "new_articles": len(new_articles),
        "total_articles": len(articles),
        "article_files_mb": round(sum(sizes.values()) / 1024 / 1024, 1),
        "processing": stats,
        "sources": reports,
        "failed_sources": [r["name"] for r in failed],
    })
    save_json(DATA_DIR / "state.json", state)

    # 모든 소스가 실패했을 때만 오류로 끝냄 (GitHub 실행 기록에 빨간 표시)
    worked = [r for r in reports if r["status"] in ("ok", "partial")]
    return 0 if worked else 2


if __name__ == "__main__":
    raise SystemExit(main())
