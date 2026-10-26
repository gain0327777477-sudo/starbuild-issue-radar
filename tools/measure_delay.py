"""반영 지연 재기 — 예약 시각(정각·30분) → 실행 시작 → 수집 끝 → 화면(Pages) 반영.

실행:  python tools/measure_delay.py --repo 아이디/저장소이름 --site https://아이디.github.io/저장소이름/
  · GitHub 공개 API(로그인 불필요)로 최근 '예약 실행(schedule)' 기록을 읽고
  · 사이트의 data/status.json·version.json 으로 화면에 실제로 반영됐는지 확인합니다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST) if ts else None


def slot_of(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 30, second=0, microsecond=0)


def minutes(a: datetime | None, b: datetime | None) -> str:
    return f"{(b - a).total_seconds() / 60:5.1f}분" if a and b else "   -  "


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="예: starbuild/starbuild-issue-radar")
    ap.add_argument("--site", required=True, help="예: https://starbuild.github.io/starbuild-issue-radar/")
    ap.add_argument("--count", type=int, default=10)
    args = ap.parse_args()

    api = f"https://api.github.com/repos/{args.repo}/actions/workflows/collect.yml/runs"
    r = requests.get(api, params={"per_page": 50}, headers={"Accept": "application/vnd.github+json"}, timeout=20)
    if r.status_code != 200:
        print(f"❌ GitHub API 응답 {r.status_code}: {r.text[:200]}")
        return 1
    runs = [x for x in r.json().get("workflow_runs", []) if x["event"] == "schedule"][: args.count]
    if not runs:
        print("아직 예약 실행(schedule) 기록이 없습니다. 첫 예약 실행은 업로드 후 30분~1시간 안에 생깁니다.")

    print("예약 시각 → 대기열 → 시작 → 끝(수집+배포)                      결과")
    print("-" * 78)
    total = []
    for x in reversed(runs):
        created, started, updated = parse(x["created_at"]), parse(x.get("run_started_at")), parse(x["updated_at"])
        slot = slot_of(created)
        print(f"{slot:%m/%d %H:%M}  대기열 +{minutes(slot, created)}  시작 +{minutes(slot, started)}  "
              f"끝 +{minutes(slot, updated)}   {x['conclusion'] or x['status']}")
        if x["conclusion"] == "success":
            total.append((updated - slot).total_seconds() / 60)
    if total:
        print("-" * 78)
        print(f"예약 시각 → 화면 반영(성공 {len(total)}회): 평균 {sum(total) / len(total):.1f}분, "
              f"가장 빠름 {min(total):.1f}분, 가장 늦음 {max(total):.1f}분")

    base = args.site.rstrip("/") + "/"
    try:
        st = requests.get(base + "data/status.json", params={"t": datetime.now().timestamp()}, timeout=20).json()
        ver = requests.get(base + "version.json", params={"t": datetime.now().timestamp()}, timeout=20).json()
        print(f"\n지금 화면에 보이는 데이터: 수집 {st.get('collected_at', '?')[:16]} · 배포 {ver.get('built_at', '?')[:16]} "
              f"(버전 {ver.get('version')})")
        run = st.get("run") or {}
        if run.get("scheduled_slot"):
            print(f"  └ 예약 {run['scheduled_slot'][11:16]} → 수집 시작 {run['started_at'][11:16]} "
                  f"(+{run['start_delay_min']}분) → 수집 끝 {run['finished_at'][11:16]}")
    except (requests.RequestException, ValueError) as e:
        print(f"\n사이트를 읽지 못했습니다: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
