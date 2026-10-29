from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunCancelled, RunContext
from kwtool.adapters.search_regional import PROVINCES, query_matches, resolve_province
from kwtool.subjects import parse_subject


SUBJECT = "中信证券股份有限公司"
SITES = {key: adapter.sites for key, adapter in registered_adapters().items()
         if key in {"regional_court", "regional_ccdi", "regional_procuratorate", "regional_samr"}}
RESULT_PATHS = {
    "regional_court": ("/search/local/208001", "/jrobot/search.do", "/article/essearch.shtml", "/shfy/web/search_zh.jsp"),
    "regional_ccdi": ("/search/all/204001", "/allsearch/", "/was5/web/search", "/TrueCMS/searchController/getResult.do"),
    "regional_procuratorate": ("/search/search.shtml", "/jcms/jcms_files/jcms1/web1/site/module/sitesearch/index.jsp", "/search.htm", "/shjcysearch.jspx"),
    "regional_samr": ("/search/all/144", "/api-gateway/jpaas-jsearch-web-server/search", "/so/s", "/kbym/"),
}


class Page:
    def __init__(self, browser, url="", body="", status=200):
        self.browser, self.url, self.body, self.status = browser, url, body, status
        self.frames = [self]
        self.closed = False
        self.query = ""
        self.filled = []
        self.selected = []

    def goto(self, url, **kwargs):
        self.url = url
        if self.browser.direct:
            self.url = self.browser.result.url
            self.body = self.browser.result.body
        return SimpleNamespace(status=self.status)

    def locator(self, selector):
        return SimpleNamespace(inner_text=lambda **kwargs: self.body,
                               fill=self.fill,
                               count=lambda: 1, input_value=lambda: SUBJECT,
                               select_option=lambda value: self.selected.append(value))

    def frame(self, name):
        return self.browser.result

    def frame_locator(self, selector):
        return SimpleNamespace(locator=lambda selector: SimpleNamespace(fill=self.fill, input_value=lambda: self.query))

    def fill(self, value):
        self.query = value
        self.filled.append(value)

    def click(self):
        request = SimpleNamespace(url=self.browser.result.url, method="POST", resource_type="document",
            post_data=urlencode({self.browser.query_key: self.query}))
        for event, listener in self.browser.listeners:
            if event == "request":
                listener(request)
        self.browser.pages.append(self.browser.result)

    def evaluate(self, script):
        if script == "do_search()":
            self.click()

    def content(self):
        return ""

    def emulate_media(self, **kwargs):
        pass

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True
        self.browser.pages.remove(self)


def context_for(tmp_path, monkeypatch, key, province, *, body="共找到 0 条结果", url=None):
    site = SITES[key][province]
    host = site.hosts[-1]
    path = RESULT_PATHS[key][PROVINCES.index(province)]
    result_url = f"https://{host}{path}?" + urlencode({site.query_keys[0]: SUBJECT}, encoding=site.encoding)
    browser = SimpleNamespace(pages=[], listeners=[], direct=bool(site.direct_query), query_key=site.query_keys[0])
    browser.on = lambda event, callback: browser.listeners.append((event, callback))
    browser.remove_listener = lambda event, callback: browser.listeners.remove((event, callback))
    browser.result = Page(browser, url or result_url, body)
    home = Page(browser)
    browser.pages = [home]
    monkeypatch.setattr("kwtool.adapters.search_regional._first_visible", lambda frame, selectors: frame)
    monkeypatch.setattr("kwtool.adapters.court_regional._first_visible", lambda frame, selectors: frame)
    monkeypatch.setattr("kwtool.adapters.court_regional.next_control", lambda *args: None)
    monkeypatch.setattr("kwtool.adapters.search_regional.result_signature", lambda scope, pager: ())
    monkeypatch.setattr("kwtool.adapters.search_regional.next_control", lambda *args: None)
    adapter = registered_adapters()[key]
    run = RunContext(browser, home, tmp_path / "result.pdf", adapter.default_pdf_settings, {},
                     Event(), lambda message: None, [], subject_kwargs={"注册地": province})
    printed = []
    run.render_pdf = lambda page: printed.append(page) or b"PDF"
    def wait(ready, message, **kwargs):
        ready()
        if not ready():
            raise TimeoutError(message)
    run.wait_until = wait
    ticks = iter(range(100))
    monkeypatch.setattr("kwtool.adapters.search_regional.time.monotonic", lambda: next(ticks))
    return adapter, run, printed


@pytest.mark.parametrize("key,province", [(key, p) for key in SITES for p in PROVINCES])
def test_all_sixteen_routes_search_name_and_export_results(tmp_path, monkeypatch, key, province):
    adapter, run, printed = context_for(tmp_path, monkeypatch, key, province)
    assert adapter.required_kwargs == ("注册地",)
    assert adapter.default_pdf_settings.scale == 0.35
    assert adapter.settings_schema[0].key == "max_pages"
    assert adapter.settings_schema[0].default == 3
    adapter.run_one(run, SUBJECT)
    assert len(printed) == 1
    if key == "regional_court" and province == "上海市":
        assert printed[0] is run.page
    else:
        assert printed[0].body == "共找到 0 条结果"
    assert run.browser.listeners == []
    assert run.browser.pages == [run.page]
    if not SITES[key][province].direct_query:
        assert run.page.filled == [SUBJECT]


@pytest.mark.parametrize("value,expected", [(p, p) for p in PROVINCES] + [
    ("广东", "广东省"), ("浙江", "浙江省"), (" 北京 ", "北京市"), ("上海", "上海市"),
])
def test_registration_syntax_and_aliases(value, expected):
    name, kwargs = parse_subject(f"{SUBJECT}[注册地:{value}]")
    assert name == SUBJECT
    assert resolve_province(kwargs["注册地"]) == expected


@pytest.mark.parametrize("province", ["", "福建省", "广东省深圳市", "未知"])
def test_missing_or_unsupported_registration_fails_before_browsing(tmp_path, monkeypatch, province):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_ccdi", "广东省")
    run.subject_kwargs = {"注册地": province}
    with pytest.raises(ValueError, match="不支持的注册地"):
        adapter.run_one(run, SUBJECT)
    assert run.page.url == ""
    assert not printed


def test_beijing_samr_empty_result_message_is_printed_without_waiting_for_count(tmp_path, monkeypatch):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_samr", "北京市",
        body="很抱歉！未能搜到您想要的内容 建议您：看看输入的文字是否有错误")
    adapter.run_one(run, SUBJECT)
    assert len(printed) == 1
    assert len(run.artifacts) == 1


@pytest.mark.parametrize("url,body", [
    ("https://unrelated.example/search?keywords=" + SUBJECT, "共找到 1 条结果"),
    ("https://www.bjsupervision.gov.cn/?searchword=" + SUBJECT, "共找到 1 条结果"),
    (None, "请完成验证 共找到 1 条结果"),
    (None, "搜索结果正在加载"),
    (None, "Access Denied"),
])
def test_unproven_or_blocked_results_are_never_printed(tmp_path, monkeypatch, url, body):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_ccdi", "北京市", body=body, url=url)
    with pytest.raises(RuntimeError):
        adapter.run_one(run, SUBJECT)
    assert not printed
    assert run.browser.listeners == []


def test_provincial_outage_does_not_abort_other_provinces(tmp_path, monkeypatch):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_ccdi", "北京市")
    run.page.status = 503
    # The worker stops a whole batch on SiteUnavailable, but continues RuntimeError rows.
    with pytest.raises(RuntimeError, match="HTTP 503"):
        adapter.run_one(run, SUBJECT)
    run.page.status = 200
    adapter.run_one(run, SUBJECT)
    assert len(printed) == 1


def test_cancellation_is_preserved(tmp_path, monkeypatch):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_ccdi", "北京市")
    run.stop_event.set()
    with pytest.raises(RunCancelled):
        adapter.run_one(run, SUBJECT)
    assert not printed


def test_shanghai_court_uses_full_search_page_and_native_gbk_form():
    site = SITES["regional_court"]["上海市"]
    assert site.url.endswith("/search_zh.jsp")
    assert site.encoding == "gbk"
    assert not site.direct_query
    url = "https://www.hshfy.sh.cn/shfy/web/search_re.jsp?" + urlencode({"nr": SUBJECT}, encoding="gbk")
    assert query_matches(url, ("nr",), SUBJECT, encoding="gbk")
    assert not query_matches(url, ("nr",), SUBJECT)


def test_shanghai_market_query_in_hash_route():
    assert query_matches("https://scjgj.sh.gov.cn/kbym/#search/query=" + SUBJECT, ("query",), SUBJECT)


def test_waits_for_inflight_results_request_before_printing(tmp_path, monkeypatch):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_court", "广东省")
    def wait(ready, message, **kwargs):
        request = type("Request", (), {"url": "https://search.gdcourts.gov.cn/api/search/site",
            "method": "GET", "resource_type": "xhr"})()
        handlers = dict(run.browser.listeners)
        handlers["request"](request)
        assert ready() is False
        assert not printed
        handlers["requestfinished"](request)
        assert ready() is False  # Start the stable-content interval.
        assert ready() is True
    run.wait_until = wait
    adapter.run_one(run, SUBJECT)
    assert len(printed) == 1


@pytest.mark.parametrize("pages", [0, -1, 1001, "abc", 1.5, True, None])
def test_invalid_page_limits_fail_before_browsing(tmp_path, monkeypatch, pages):
    adapter, run, printed = context_for(tmp_path, monkeypatch, "regional_ccdi", "北京市")
    run.site_settings["max_pages"] = pages
    with pytest.raises(ValueError, match="前 K 页"):
        adapter.run_one(run, SUBJECT)
    assert not printed
    assert run.page.url == ""
