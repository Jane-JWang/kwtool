from pathlib import Path
from threading import Event

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.neris import HOME, NerisAdapter, _find_result, _page_snapshot, _result_page


def test_result_page_requires_neris_host_and_search_result_path() -> None:
    assert _result_page("https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name=测试")
    assert not _result_page("https://neris.csrc.gov.cn/shixinchaxun/")
    assert not _result_page("https://other.example/shixinchaxun/searchResult")


def test_result_lookup_includes_other_chrome_contexts() -> None:
    class Page:
        def __init__(self, url):
            self.url = url

        def is_closed(self):
            return False

    class BrowserContext:
        def __init__(self, pages):
            self.pages = pages
            self.browser = None

    search = Page(HOME)
    result = Page("https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name=主体甲")
    primary = BrowserContext([search])
    secondary = BrowserContext([result])
    chrome = type("Chrome", (), {"contexts": [primary, secondary]})()
    primary.browser = chrome
    assert _find_result(type("Context", (), {"browser": primary})(), "主体甲") is result
    assert _find_result(type("Context", (), {"browser": primary})(), "主体乙") is None
    result.url += "&verification=secret-token"
    assert "secret-token" not in _page_snapshot(type("Context", (), {"browser": primary})(), "主体甲")


def test_each_query_waits_for_manual_verification_before_printing(tmp_path: Path) -> None:
    class Element:
        def __init__(self, page, kind):
            self.page = page
            self.kind = kind
            self.first = self

        def is_visible(self):
            return True

        def fill(self, text):
            self.page.filled.append(text)

        def click(self):
            self.page.clicked += 1

        def inner_text(self, **kwargs):
            return "查询结果\n无符合条件记录"

    class Page:
        url = "about:blank"

        def __init__(self):
            self.filled = []
            self.clicked = 0
            self.pdf_options = []
            self.closed = False

        def goto(self, url, **kwargs):
            assert url == HOME
            self.url = url
            return None

        def locator(self, selector):
            return Element(self, selector)

        def get_by_text(self, text, **kwargs):
            assert text == "查询结果"
            return Element(self, text)

        def evaluate(self, expression):
            assert expression in {"document.fonts.ready", "window.scrollTo(0, 0)"}

        def emulate_media(self, **kwargs):
            assert kwargs == {"media": "screen"}

        def pdf(self, **kwargs):
            self.pdf_options.append(kwargs)
            return b"%PDF-1.4\n"

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def is_closed(self):
            return self.closed

        def close(self):
            self.closed = True

        def bring_to_front(self):
            pass

    class Browser:
        browser = None

        def __init__(self, page):
            self.pages = [page]

    adapter = registered_adapters()["neris"]
    assert isinstance(adapter, NerisAdapter)
    page = Page()
    browser = Browser(page)
    results = []
    context = RunContext(
        browser=browser,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "first.pdf",
        pdf_settings=adapter.default_pdf_settings,
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
    )
    waits = []

    def wait_until(ready, message, **kwargs):
        waits.append(kwargs.get("user_action", False))
        if kwargs.get("user_action"):
            assert page.clicked == len(page.filled)
            assert not ready()
            result = Page()
            result.url = f"https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name={page.filled[-1]}"
            browser.pages.append(result)
            results.append(result)
        assert ready()

    context.wait_until = wait_until  # type: ignore[method-assign]
    adapter.run_one(context, "主体甲")
    context.pdf_path = tmp_path / "second.pdf"
    adapter.run_one(context, "主体乙")
    assert page.filled == ["主体甲", "主体乙"]
    assert waits == [False, True, False, False, True, False]
    assert len(context.artifacts) == 2
    assert all(result.closed for result in results)
    assert all(len(result.pdf_options) == 1 for result in results)
    assert all(result.pdf_options[0]["landscape"] is False and result.pdf_options[0]["scale"] == 0.35 for result in results)
