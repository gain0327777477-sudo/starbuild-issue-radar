"""인터넷 요청. 실패하면 원인을 알 수 있는 한국어 메시지를 만든다(키는 가림)."""
from __future__ import annotations

import re
import time

import requests

from .env import mask

USER_AGENT = "Mozilla/5.0 (compatible; StarbuildIssueRadar/1.0; +https://blog.naver.com/yantai7)"
TIMEOUT = 15


class SourceError(Exception):
    """소스 하나의 요청 실패. status 는 HTTP 상태 코드(접속 자체 실패면 None)."""

    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(mask(message))


def body_summary(text: str) -> str:
    """오류 응답 내용 요약. HTML 페이지면 <title>만, 아니면 앞 300자(네이버 API는 JSON으로 원인을 알려줌)."""
    if "<html" in text[:500].lower():
        m = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        return f"HTML 페이지 '{m.group(1).strip()[:80]}'" if m else "HTML 페이지"
    return text[:300].replace("\n", " ")


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None, retries: int = 1,
             retry_statuses: tuple[int, ...] = ()) -> requests.Response:
    """GET 요청. 접속 실패·5xx(와 retry_statuses)는 2초 쉬고 한 번 더 시도."""
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    last: SourceError | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
        except requests.Timeout:
            last = SourceError(f"응답 없음({TIMEOUT}초 초과)")
        except requests.RequestException as e:
            last = SourceError(f"접속 실패: {type(e).__name__} {e}")
        else:
            if r.status_code < 400:
                return r
            last = SourceError(f"HTTP {r.status_code} 응답: {body_summary(r.text)}", status=r.status_code)
            if r.status_code < 500 and r.status_code not in retry_statuses:
                break           # 4xx 는 다시 해도 같음
        if attempt < retries:
            time.sleep(2)
    raise last
