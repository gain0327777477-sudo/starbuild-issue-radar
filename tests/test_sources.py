"""2단계 수집기 테스트 — 인터넷에 접속하지 않고 가짜 응답으로 확인."""
from datetime import datetime

import pytest

from collector import main as collector_main
from collector.config import load_config
from collector.http import SourceError
from collector.models import KST, Article
from collector.sources import blog_rss, google_news, google_rss, korea_briefing, naver_news
from collector.sources.base import Context, SkipSource
from collector.store import merge_articles

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=KST)

GOOGLE_XML = """<?xml version="1.0"?><rss><channel>
<item><title>3기 신도시 도로공사 인·허가 기간 단축 - 대한민국 정책브리핑</title>
<link>https://news.google.com/rss/articles/abc</link><pubDate>Mon, 26 Oct 2026 05:58:35 GMT</pubDate>
<source url="https://www.korea.kr">대한민국 정책브리핑</source></item>
<item><title>내년부터 친환경농업 정책 결정에 참여 - 정책뉴스 | 뉴스 - 대한민국 정책브리핑</title>
<link>https://news.google.com/rss/articles/def</link><pubDate>Mon, 26 Oct 2026 04:00:00 GMT</pubDate>
<source url="https://www.korea.kr">대한민국 정책브리핑</source></item>
</channel></rss>"""

NAVER_JSON = {"items": [{
    "title": "<b>기준금리</b> 동결&quot;", "originallink": "https://www.hankyung.com/article/1",
    "link": "https://n.news.naver.com/1", "description": "한국은행이 <b>기준금리</b>를 동결했다",
    "pubDate": "Mon, 26 Oct 2026 10:00:00 +0900"}]}

BLOG_XML = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
<item><title><![CDATA[잔금 전에 건물이 불탔다]]></title><link>https://blog.naver.com/yantai7/1?fromRss=true</link>
<category><![CDATA[뉴스]]></category><pubDate>Mon, 26 Oct 2026 09:44:52 +0900</pubDate></item>
</channel></rss>"""


class FakeResp:
    def __init__(self, text="", data=None):
        self.text = text
        self.content = text.encode("utf-8")
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def cfg():
    c = load_config()
    c.sources["google_news"]["pause_seconds"] = 0
    return c


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "myid12345")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "mysecret987")


def test_google_rss_parse_strips_press_and_briefing_tail():
    arts = google_rss.parse_rss(GOOGLE_XML, source="briefing", query="q", field=None, fetched_at="t", agency="국토교통부")
    assert [a.title for a in arts] == ["3기 신도시 도로공사 인·허가 기간 단축", "내년부터 친환경농업 정책 결정에 참여"]
    assert arts[0].published == "2026-10-26T14:58:35+09:00"      # GMT → 한국 시간
    assert arts[0].domain == "korea.kr" and arts[0].agency == "국토교통부"


def test_google_rss_bad_response_has_readable_error():
    with pytest.raises(SourceError) as e:
        google_rss.parse_rss("<html>captcha", source="google", query="q", field=None, fetched_at="t")
    assert "RSS 형식이 아님" in str(e.value) and "captcha" in str(e.value)


def test_briefing_query_includes_old_agency_names(cfg):
    qs = dict(korea_briefing.build_queries(cfg))
    assert qs["재정경제부"] == "site:korea.kr (재정경제부 OR 기획재정부 OR 기재부 OR 재경부) when:3d"
    assert qs["국세청"] == "site:korea.kr 국세청 when:3d"


def test_or_query_quotes_phrases():
    assert google_rss.or_query(["미국 증시", "나스닥"]) == '("미국 증시" OR 나스닥)'


def test_google_runs_all_queries_every_run(cfg):
    """2시간 간격 실행이라 순환 없이 매번 전체 검색어를 한 바퀴."""
    sent = []
    state = {"google_round": 3}                                                  # 예전 순환 기록이 있어도
    res = google_news.collect(Context(cfg, state, NOW, get=lambda url, params=None, **k: sent.append(params["q"]) or FakeResp(GOOGLE_XML)))
    assert sorted(sent) == sorted(q["q"] for q in google_news.build_queries(cfg))
    assert "google_round" not in state and res.calls == len(sent)


def test_naver_morning_run_fetches_more(cfg, keys):
    cfg.company_combos = {}
    cfg.fields = {k: v for k, v in cfg.fields.items() if k == "rate"}
    displays = []
    get = lambda url, params=None, **k: displays.append(params["display"]) or FakeResp(data={"items": []})
    morning = datetime(2026, 10, 27, 6, 3, tzinfo=KST)
    res = naver_news.collect(Context(cfg, {"last_success": {"naver_news": "2026-10-26T22:04:00+09:00"}}, morning, get=get))
    assert set(displays) == {100} and "아침 첫 실행" in res.notes[0]
    displays.clear()
    day = datetime(2026, 10, 27, 10, 5, tzinfo=KST)
    naver_news.collect(Context(cfg, {"last_success": {"naver_news": "2026-10-27T08:04:00+09:00"}}, day, get=get))
    assert set(displays) == {20}
    displays.clear()                                                              # 낮인데 실행이 빠져 5시간 넘게 비면 많이
    naver_news.collect(Context(cfg, {"last_success": {"naver_news": "2026-10-27T08:04:00+09:00"}},
                               datetime(2026, 10, 27, 14, 5, tzinfo=KST), get=get))
    assert set(displays) == {100}


def test_local_gov_queries_have_region(cfg):
    local = [q for q in google_news.build_queries(cfg) if q["source"] == "local_gov"]
    assert {q["region_hint"] for q in local} == {"서울", "경기", "인천", "충남", "부산", "대전"}


def test_naver_skipped_without_keys(cfg, no_keys):
    with pytest.raises(SkipSource) as e:
        naver_news.collect(Context(cfg, {}, NOW, get=lambda *a, **k: pytest.fail("키 없이 요청하면 안 됨")))
    assert "키 없음" in str(e.value)


def test_naver_parse_and_daily_counter(cfg, keys):
    cfg.company_combos = {}
    cfg.fields = {k: v for k, v in cfg.fields.items() if k == "rate"}
    state = {}
    res = naver_news.collect(Context(cfg, state, NOW, get=lambda *a, **k: FakeResp(data=NAVER_JSON)))
    a = res.articles[0]
    assert a.title == '기준금리 동결"' and a.url == "https://www.hankyung.com/article/1"
    assert a.press == "한국경제" and a.summary == "한국은행이 기준금리를 동결했다" and a.field == "rate"
    assert state["naver_calls"] == {"date": "2026-10-26", "count": len(cfg.fields["rate"].keywords)}


def test_naver_stops_at_daily_limit(cfg, keys):
    cfg.sources["naver_news"]["daily_call_limit"] = 10
    state = {"naver_calls": {"date": "2026-10-26", "count": 7}}
    calls = []
    res = naver_news.collect(Context(cfg, state, NOW, get=lambda *a, **k: calls.append(1) or FakeResp(data={"items": []})))
    assert len(calls) == 3 and "3개만" in res.notes[0]
    with pytest.raises(SkipSource):
        naver_news.collect(Context(cfg, state, NOW, get=lambda *a, **k: FakeResp(data={"items": []})))


def test_naver_auth_error_shows_api_message_but_hides_key(cfg, keys):
    def bad(*a, **k):
        raise SourceError('HTTP 401 응답: {"errorMessage":"Not Exist Client ID : Authentication failed. (mysecret987)","errorCode":"024"}', 401)
    with pytest.raises(SourceError) as e:
        naver_news.collect(Context(cfg, {}, NOW, get=bad))
    msg = str(e.value)
    assert "키 인증 실패" in msg and "errorCode" in msg and "024" in msg
    assert "mysecret987" not in msg and "my****" in msg


def test_google_block_pauses_both_google_sources(cfg):
    def blocked(*a, **k):
        raise SourceError("HTTP 503 응답: unusual traffic", 503)
    state = {}
    with pytest.raises(SourceError):
        korea_briefing.collect(Context(cfg, state, NOW, get=blocked))
    assert state["google_block_until"].startswith("2026-10-26T13:00")
    with pytest.raises(SkipSource):
        google_news.collect(Context(cfg, state, NOW, get=blocked))


def test_blog_posts_parse():
    posts = blog_rss.parse_posts(BLOG_XML)
    assert posts == [{"title": "잔금 전에 건물이 불탔다", "url": "https://blog.naver.com/yantai7/1",
                      "published": "2026-10-26T09:44:52+09:00", "category": "뉴스"}]


def test_one_source_failing_does_not_stop_others(cfg, no_keys, monkeypatch, tmp_path):
    """정책브리핑이 고장 나도 구글뉴스·블로그는 계속 수집되고, 실패 내역이 status 에 남는다."""
    def fake_get(url, params=None, headers=None, **kw):
        if params and "site:korea.kr" in params.get("q", ""):
            raise SourceError("HTTP 500 응답: server error", 500)
        if "rss.blog.naver.com" in url:
            return FakeResp(BLOG_XML)
        return FakeResp(GOOGLE_XML.replace("대한민국 정책브리핑", "연합뉴스").replace("korea.kr", "yna.co.kr"))

    monkeypatch.setattr(collector_main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collector_main, "load_config", lambda: cfg)
    monkeypatch.setattr(collector_main, "Context", lambda cfg, state, now: Context(cfg, state, now, get=fake_get))
    assert collector_main.main() == 0

    import json
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in status["sources"]}
    assert by_id["naver_news"]["status"] == "skipped"
    assert by_id["korea_briefing"]["status"] == "failed" and "HTTP 500" in by_id["korea_briefing"]["message"]
    assert by_id["google_news"]["status"] == "ok" and by_id["google_news"]["count"] > 0
    assert by_id["blog_rss"]["status"] == "ok"
    assert status["failed_sources"] == ["정책브리핑"]
    assert (tmp_path / "articles.json").exists() and (tmp_path / "blog_posts.json").exists()


def test_error_body_html_shows_only_title():
    from collector.http import body_summary
    page = '<html lang="ko"><meta charset=utf-8><title>Error 404 (찾을 수 없음)!!1</title><style>*{margin:0}</style>'
    assert body_summary(page) == "HTML 페이지 'Error 404 (찾을 수 없음)!!1'"
    assert body_summary('{"errorCode":"024"}') == '{"errorCode":"024"}'


def test_google_404_is_retried_once(monkeypatch):
    from collector import http
    calls = []

    class R:
        def __init__(self, code):
            self.status_code, self.text = code, "<html><title>Error 404</title>" if code == 404 else "<rss/>"

    monkeypatch.setattr(http.requests, "get", lambda *a, **k: calls.append(1) or R(404 if len(calls) == 1 else 200))
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.http_get("https://news.google.com/rss/search", retry_statuses=(404,)).status_code == 200
    assert len(calls) == 2


def _art(title, source, published, url=None):
    return Article(id="x", title=title, url=url or f"https://e.com/{title}/{source}", press="p", domain="e.com",
                   source=source, published=published, summary="", query="q", field=None, fetched_at="t")


def test_merge_keeps_naver_copy_and_drops_old():
    old = [_art("오래된 기사", "google", "2026-10-10T10:00:00+09:00")]
    new = [_art("기준금리 동결!", "google", "2026-10-26T10:00:00+09:00"),
           _art("기준금리  동결", "naver", "2026-10-26T10:00:00+09:00")]
    out = merge_articles(old, new, NOW, retention_days=7)
    assert len(out) == 1 and out[0].source == "naver"
