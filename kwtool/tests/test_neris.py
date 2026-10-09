from pathlib import Path
from threading import Event

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.neris import HOME, NerisAdapter, _result_page


def test_result_page_requires_neris_host_and_search_result_path() -> None:
    assert _result_page("https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name=测试")
    assert not _result_page("https://neris.csrc.gov.cn/shixinchaxun/")
    assert not _result_page("https://other.example/shixinchaxun/searchResult")


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

    class Page:
        url = "about:blank"

        def __init__(self):
            self.filled = []
            self.clicked = 0
            self.pdf_options = []

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

    adapter = registered_adapters()["neris"]
    assert isinstance(adapter, NerisAdapter)
    page = Page()
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
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
            page.url = "https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name=测试"
        assert ready()

    context.wait_until = wait_until  # type: ignore[method-assign]
    adapter.run_one(context, "主体甲")
    context.pdf_path = tmp_path / "second.pdf"
    adapter.run_one(context, "主体乙")
    assert page.filled == ["主体甲", "主体乙"]
    assert waits == [False, True, False, True]
    assert len(context.artifacts) == 2
    assert all(options["landscape"] is False and options["scale"] == 0.35 for options in page.pdf_options)
