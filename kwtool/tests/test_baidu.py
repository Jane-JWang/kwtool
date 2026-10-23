"""Baidu search configuration and ordered PDF export."""

from io import BytesIO
from itertools import chain, repeat
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader, PdfWriter

from kwtool.adapters import registered_adapters
from kwtool.adapters.baidu import BaiduAdapter, _click_result_page, _open_search, _result_snapshot, search_url
from kwtool.adapters.base import RunContext


def test_baidu_registration_and_query() -> None:
    adapter = registered_adapters()["baidu"]
    assert isinstance(adapter, BaiduAdapter)
    assert adapter.default_pdf_settings.scale == 0.35
    assert [(field.key, field.default) for field in adapter.settings_schema] == [
        ("common_keyword", ""),
        ("keywords", "立案 调查 监管 处罚"), ("max_pages", 2),
        ("page_settle_seconds", 0.75), ("result_settle_seconds", 0.75)]
    parsed = urlparse(search_url("某公司 处罚", 3))
    assert parsed.hostname == "www.baidu.com"
    assert parse_qs(parsed.query) == {"wd": ["某公司 处罚"], "pn": ["20"]}


def test_baidu_submits_search_box() -> None:
    actions = []

    class Locator:
        def __init__(self, selector):
            self.selector = selector

        def is_visible(self):
            return True

        def fill(self, value):
            actions.append((self.selector, "fill", value))

        def click(self):
            actions.append((self.selector, "click"))

    class Page:
        def goto(self, url, **_kwargs):
            actions.append(("goto", url))

        def locator(self, selector):
            return Locator(selector)

        def wait_for_url(self, predicate, **_kwargs):
            assert predicate(search_url("a bar", 1))

    _open_search(Page(), "a bar")  # type: ignore[arg-type]
    assert actions == [
        ("goto", "https://www.baidu.com/"),
        ("#chat-textarea", "fill", "a bar"),
        ("#chat-submit-button", "click"),
    ]


def test_baidu_submits_legacy_search_box_when_new_one_is_hidden() -> None:
    actions = []

    class Locator:
        def __init__(self, selector):
            self.selector = selector

        def is_visible(self):
            return False

        def fill(self, value):
            actions.append((self.selector, "fill", value))

        def click(self):
            actions.append((self.selector, "click"))

    class Page:
        def goto(self, _url, **_kwargs):
            return None

        def locator(self, selector):
            return Locator(selector)

        def wait_for_url(self, predicate, **_kwargs):
            assert predicate(search_url("a bar", 1))

    _open_search(Page(), "a bar")  # type: ignore[arg-type]
    assert actions == [("#kw:visible", "fill", "a bar"), ("#su:visible", "click")]


def test_baidu_uses_page_link_for_next_result_page() -> None:
    query = "a bar"

    class Page:
        url = search_url(query, 1)

        def evaluate(self, _script):
            return {"results": ["First result"], "next": [
                {"href": "/s?wd=a+bar&pn=10&rsv_pq=browser-value", "text": "2"},
            ]}

    results, page_urls = _result_snapshot(Page(), query)  # type: ignore[arg-type]
    assert results == ("First result",)
    assert page_urls == {2: "https://www.baidu.com/s?wd=a+bar&pn=10&rsv_pq=browser-value"}


def test_baidu_ignores_pagination_link_for_another_query() -> None:
    class Page:
        url = search_url("gibberish bar", 1)

        def evaluate(self, _script):
            return {"results": ["First result"], "ai": "", "next": [
                {"href": "/s?wd=bar&pn=10", "text": "2"},
                {"href": "/s?wd=gibberish+bar&pn=20", "text": "3"},
            ]}

    _, page_urls = _result_snapshot(Page(), "gibberish bar")  # type: ignore[arg-type]
    assert set(page_urls) == {3}


def test_baidu_clicks_numbered_pagination_link(monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    actions = []
    clock = iter(range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))

    class Link:
        def get_by_role(self, role, **kwargs):
            actions.append((role, kwargs))
            return self

        def click(self):
            actions.append("click")

    class Page:
        def locator(self, selector):
            assert selector == "#page"
            return Link()

        def wait_for_url(self, predicate, **_kwargs):
            assert predicate(search_url("a bar", 3))

        def evaluate(self, _script):
            return 3

        def wait_for_timeout(self, _ms):
            pass

    _click_result_page(Page(), "a bar", 3)  # type: ignore[arg-type]
    assert actions == [("link", {"name": "3", "exact": True}), "click"]


def test_baidu_retries_when_results_revert_to_first_page(monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    clock = iter(range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    clicks = []

    class Link:
        def get_by_role(self, *_args, **_kwargs):
            return self

        def click(self):
            clicks.append(1)

    class Page:
        def locator(self, _selector):
            return Link()

        def wait_for_url(self, predicate, **_kwargs):
            assert predicate(search_url("a bar", 2))

        def evaluate(self, _script):
            return 1 if len(clicks) == 1 else 2

        def wait_for_timeout(self, _ms):
            pass

    _click_result_page(Page(), "a bar", 2)  # type: ignore[arg-type]
    assert len(clicks) == 2


def test_baidu_accepts_completed_navigation_when_click_times_out(monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    clock = iter(range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    clicks = []

    class Link:
        def get_by_role(self, *_args, **_kwargs):
            return self

        def click(self):
            clicks.append(1)
            raise module.PlaywrightTimeoutError("temporary mask intercepted click")

    class Page:
        url = search_url("a bar", 5)

        def locator(self, _selector):
            return Link()

        def wait_for_url(self, predicate, **_kwargs):
            assert predicate(self.url)

        def evaluate(self, _script):
            return 5

        def wait_for_timeout(self, _ms):
            pass

    _click_result_page(Page(), "a bar", 5)  # type: ignore[arg-type]
    assert clicks == [1]


def test_baidu_exports_keywords_and_pages_in_order(tmp_path: Path, monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    class Page:
        url = ""
        printed: list[tuple[str, int]] = []
        history: list[str] = []

        def goto(self, url, **_kwargs):
            self.url = url
            self.history.append(url)
            return None

        def go_back(self, **_kwargs):
            self.history.pop()
            self.url = self.history[-1]
            return None

        def evaluate(self, _script, *_args):
            return None

        def emulate_media(self, **_kwargs):
            return None

    page = Page()
    page_waits = []
    result_waits = []
    monkeypatch.setattr(module, "_open_search", lambda page, query: page.goto(search_url(query, 1)))
    def click_result_page(page, query, number, _check_stop, settle):
        page_waits.append(settle)
        page.goto(search_url(query, number))

    monkeypatch.setattr(module, "_click_result_page", click_result_page)
    monkeypatch.setattr(module, "_result_state", lambda _page, _query, _number: "results")

    def snapshot(_context, query, settle):
        result_waits.append(settle)
        pn = int(parse_qs(urlparse(_context.page.url).query).get("pn", ["0"])[0])
        links = {number: search_url(query, number) for number in range(2, 4)} if pn == 0 else {}
        return ((f"result-{pn}",), links)

    monkeypatch.setattr(module, "_wait_for_complete_results", snapshot)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)

    context = RunContext(None, page, tmp_path / "result.pdf", BaiduAdapter.default_pdf_settings,
                         {"keywords": "立案 调查 立案", "max_pages": 3,
                          "page_settle_seconds": 1.25, "result_settle_seconds": 0.5},
                         Event(), lambda _: None, [])  # type: ignore[arg-type]

    def render_pdf(_page):
        params = parse_qs(urlparse(page.url).query)
        page.printed.append((params["wd"][0], int(params["pn"][0])))
        return pdf.getvalue()

    monkeypatch.setattr(context, "render_pdf", render_pdf)
    BaiduAdapter().run_one(context, "某公司")
    assert page.printed == [
        ("某公司 立案", 0), ("某公司 立案", 10), ("某公司 立案", 20),
        ("某公司 调查", 0), ("某公司 调查", 10), ("某公司 调查", 20),
    ]
    assert len(PdfReader(context.artifacts[0]).pages) == 6
    assert page_waits == [1.25] * 4
    assert result_waits == [0.5] * 6


def test_baidu_common_keyword_applies_to_each_subject_and_keyword(tmp_path: Path, monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    class Page:
        url = ""

        def goto(self, url, **_kwargs):
            self.url = url
            return None

        def evaluate(self, _script, *_args):
            return None

        def emulate_media(self, **_kwargs):
            return None

    page = Page()
    monkeypatch.setattr(module, "_open_search", lambda page, query: page.goto(search_url(query, 1)))
    monkeypatch.setattr(module, "_click_result_page",
                        lambda page, query, number, _check_stop, _settle: page.goto(search_url(query, number)))
    monkeypatch.setattr(module, "_result_state", lambda _page, _query, _number: "results")
    monkeypatch.setattr(module, "_wait_for_complete_results",
                        lambda _context, query, _settle: ((query,), {}))
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)
    queries = []
    context = RunContext(None, page, tmp_path / "result.pdf", BaiduAdapter.default_pdf_settings,
                         {"common_keyword": "  foo  ", "keywords": "bar baz", "max_pages": 1},
                         Event(), lambda _: None, [])  # type: ignore[arg-type]

    def render_pdf(_page):
        queries.append(parse_qs(urlparse(page.url).query)["wd"][0])
        return pdf.getvalue()

    monkeypatch.setattr(context, "render_pdf", render_pdf)
    for subject in ("a", "b", "c"):
        BaiduAdapter().run_one(context, subject)

    assert queries == [
        "foo a bar", "foo a baz", "foo b bar", "foo b baz", "foo c bar", "foo c baz",
    ]


def test_baidu_stops_at_last_page_and_skips_repeated_results(tmp_path: Path, monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    class Page:
        url = ""

        def goto(self, url, **_kwargs):
            self.url = url
            return None

        def evaluate(self, _script, *_args):
            return None

        def emulate_media(self, **_kwargs):
            return None

    page = Page()
    monkeypatch.setattr(module, "_open_search", lambda page, query: page.goto(search_url(query, 1)))
    monkeypatch.setattr(module, "_click_result_page",
                        lambda page, query, number, _check_stop, _settle: page.goto(search_url(query, number)))
    monkeypatch.setattr(module, "_result_state", lambda _page, _query, _number: "results")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)
    printed = []
    context = RunContext(None, page, tmp_path / "result.pdf", BaiduAdapter.default_pdf_settings,
                         {"keywords": "bar baz", "max_pages": 5}, Event(), lambda _: None, [])  # type: ignore[arg-type]

    def snapshot(_context, query, _settle):
        number = int(parse_qs(urlparse(_context.page.url).query).get("pn", ["0"])[0]) // 10 + 1
        if query.endswith("bar"):
            return ((f"bar-{number}",), {2: search_url(query, 2)} if number == 1 else {})
        return (("baz-first",), {2: search_url(query, 2)})

    def render_pdf(_page):
        params = parse_qs(urlparse(page.url).query)
        printed.append((params["wd"][0], int(params["pn"][0])))
        return pdf.getvalue()

    monkeypatch.setattr(module, "_wait_for_complete_results", snapshot)
    monkeypatch.setattr(context, "render_pdf", render_pdf)
    BaiduAdapter().run_one(context, "a")

    assert printed == [("a bar", 0), ("a bar", 10), ("a baz", 0)]
    assert len(PdfReader(context.artifacts[0]).pages) == 3


def test_baidu_waits_for_search_content_before_export(tmp_path: Path, monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    class Page:
        url = ""

        def goto(self, url, **_kwargs):
            self.url = url
            return None

        def evaluate(self, _script, *_args):
            return None

        def emulate_media(self, **_kwargs):
            return None

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)
    context = RunContext(None, Page(), tmp_path / "result.pdf", BaiduAdapter.default_pdf_settings,
                         {"keywords": "bar", "max_pages": 1}, Event(), lambda _: None, [])  # type: ignore[arg-type]
    states = chain(("loading", "loading"), repeat("results"))
    printed = []
    monkeypatch.setattr(module, "_open_search", lambda page, query: page.goto(search_url(query, 1)))
    monkeypatch.setattr(module, "_click_result_page",
                        lambda page, query, number, _check_stop, _settle: page.goto(search_url(query, number)))
    monkeypatch.setattr(module, "_result_state", lambda *_args: next(states))
    monkeypatch.setattr(module, "_wait_for_complete_results", lambda *_args: (("result",), {}))

    def wait_until(ready, _message, **_kwargs):
        assert ready() is False
        assert ready() is True

    def render_pdf(_page):
        printed.append(context.page.url)
        return pdf.getvalue()

    monkeypatch.setattr(context, "wait_until", wait_until)
    monkeypatch.setattr(context, "render_pdf", render_pdf)
    BaiduAdapter().run_one(context, "a")

    assert printed == [search_url("a bar", 1)]
