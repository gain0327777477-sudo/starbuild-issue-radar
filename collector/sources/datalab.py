"""4. 네이버 데이터랩 검색어트렌드 — 상위 이슈 핵심 키워드의 검색 추이가 오르는지 (상대 추이).

하루 한도 1,000회라서:
  - 직전 정기 조회에서 interval_hours(1.5시간)가 지났으면 상위 top_issues(20)개를 5개씩 묶어 조회(=4회)
    → 수집이 2시간 간격(하루 9회)이라 사실상 매 실행마다 조회
  - 그렇지 않은 실행에서는 저장된 결과를 재사용하고, 새로 상위에 오른 이슈만 최대 max_new_calls_per_run(1)회 추가 조회
  - 스스로 건 하루 상한 daily_call_limit(200)
'실제 조회수·실시간 순위'가 아니라 최근 3일 평균이 그 전 7일 평균보다 20% 이상 높은지만 본다.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..env import get_secret, has_naver_keys
from ..http import SourceError
from .base import Context, SkipSource

ID = "datalab"
NAME = "검색 추이(데이터랩)"
URL = "https://openapi.naver.com/v1/datalab/search"
RISE_RATIO = 1.2


def trend_key(keywords: list[str]) -> str:
    return "|".join(sorted(set(keywords)))


def judge(data: list[dict]) -> dict:
    """데이터랩 일별 ratio 목록 → 상승 여부."""
    values = [d.get("ratio", 0) for d in data]
    recent = values[-3:]
    before = values[-10:-3]
    r_avg = sum(recent) / len(recent) if recent else 0
    b_avg = sum(before) / len(before) if before else 0
    rising = r_avg > 0 and (b_avg == 0 or r_avg >= b_avg * RISE_RATIO)
    return {"rising": rising, "recent": round(r_avg, 1), "before": round(b_avg, 1)}


def _calls_today(st: dict, now: datetime) -> int:
    c = st.get("calls") or {}
    return c.get("count", 0) if c.get("date") == now.date().isoformat() else 0


def plan_calls(targets: list[list[str]], st: dict, now: datetime, conf: dict) -> list[list[list[str]]]:
    """이번 회차에 조회할 키워드 묶음들 → [[묶음, 묶음, …(최대 5)], …] (안쪽 리스트 하나 = 호출 1회)."""
    cache = st.get("cache") or {}
    last_full = st.get("last_full")
    full_due = not last_full or now - datetime.fromisoformat(last_full) >= timedelta(hours=conf["interval_hours"])
    if full_due:
        todo = targets[: conf["top_issues"]]
        max_calls = math.ceil(len(todo) / conf["groups_per_call"])
    else:
        todo = [t for t in targets[: conf["top_issues"]] if trend_key(t) not in cache]
        max_calls = conf["max_new_calls_per_run"]
    room = conf["daily_call_limit"] - _calls_today(st, now)
    max_calls = max(0, min(max_calls, room))
    g = conf["groups_per_call"]
    batches = [todo[i:i + g] for i in range(0, len(todo), g)][:max_calls]
    return batches


def update(ctx: Context, targets: list[list[str]], get=None) -> dict:
    """targets: 상위 이슈 순서대로 핵심 키워드 목록. 결과는 ctx.state['datalab'] 에 쌓고 보고서를 돌려준다."""
    conf = ctx.cfg.sources["datalab"]
    if not conf.get("enabled"):
        raise SkipSource("설정에서 꺼짐")
    if not has_naver_keys():
        raise SkipSource("네이버 키 없음 — 검색 추이 점수 없이 계산")
    st = ctx.state.setdefault("datalab", {})
    now = ctx.now
    batches = plan_calls(targets, st, now, conf)
    full_due = not st.get("last_full") or now - datetime.fromisoformat(st["last_full"]) >= timedelta(hours=conf["interval_hours"])
    if not batches:
        return {"calls": 0, "message": "저장된 결과 재사용 (새로 조회할 이슈 없음)"}

    headers = {"X-Naver-Client-Id": get_secret("NAVER_CLIENT_ID"), "X-Naver-Client-Secret": get_secret("NAVER_CLIENT_SECRET"),
               "Content-Type": "application/json"}
    end = (now - timedelta(days=1)).date()           # 오늘은 하루가 덜 끝나서 어제까지
    start = end - timedelta(days=13)
    cache = st.setdefault("cache", {})
    calls = 0
    try:
        for batch in batches:
            body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
                    "keywordGroups": [{"groupName": trend_key(k)[:50], "keywords": k[:20]} for k in batch]}
            try:
                r = (get or ctx.get)(URL, headers=headers, json_body=body)
            except SourceError as e:
                calls += 1
                if e.status in (401, 403):
                    raise SourceError(f"데이터랩 권한 없음 — 네이버 개발자센터 앱에 '데이터랩(검색어트렌드)' API를 추가했는지 확인하세요. {e}", e.status)
                raise
            calls += 1
            for keywords, res in zip(batch, r.json().get("results") or []):
                cache[trend_key(keywords)] = {**judge(res.get("data") or []), "checked_at": now.isoformat()}
    finally:
        st["calls"] = {"date": now.date().isoformat(), "count": _calls_today(st, now) + calls}
    if full_due:
        st["last_full"] = now.isoformat()
    # 하루 지난 결과는 지움
    cutoff = (now - timedelta(hours=24)).isoformat()
    for k in [k for k, v in cache.items() if v["checked_at"] < cutoff]:
        del cache[k]
    return {"calls": calls, "message": ("정기 조회" if full_due else "새 상위 이슈 추가 조회") + f" {calls}회 (오늘 {st['calls']['count']}회)"}


def lookup(state: dict, keywords: list[str]) -> dict | None:
    return ((state.get("datalab") or {}).get("cache") or {}).get(trend_key(keywords))
