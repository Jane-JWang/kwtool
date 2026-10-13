from pathlib import Path
from threading import Event

from kwtool.adapters.base import RunContext
from kwtool.adapters.cnabs import CnabsAdapter, PRODUCT_URL, _search_state


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1 if self.selector.startswith('input[placeholder*="产品名称"]') else 0

    def inner_text(self, **kwargs):
        return self.page.body

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        pass

    def fill(self, value):
        self.page.query = value

    def press(self, key):
        self.page.pressed = key


class FakePage:
    url = "about:blank"
    body = "市场总览"
    result = False

    def goto(self, url, **kwargs):
        self.url = url
        return None

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, delay):
        pass

    def evaluate(self, script, *args):
        if args:
            return {"matched": self.result, "empty": False, "query": True}

    def emulate_media(self, **kwargs):
        pass

    def pdf(self, **kwargs):
        self.pdf_options = kwargs
        return b"%PDF-1.4\n"


def test_cnabs_needs_actual_matching_result() -> None:
    page = FakePage()
    page.url = PRODUCT_URL
    assert _search_state(page, "测试公司") == "loading"
    page.result = True
    assert _search_state(page, "测试公司") == "results"


def test_cnabs_rejects_login_page() -> None:
    page = FakePage()
    page.url = "https://account.cn-abs.com/account.html"
    page.result = True
    assert _search_state(page, "测试公司") == "other-page"


def test_cnabs_prints_results_with_browser_style_pdf(tmp_path: Path) -> None:
    adapter = CnabsAdapter()
    page = FakePage()
    page.result = True
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "cnabs.pdf",
        pdf_settings=adapter.default_pdf_settings,
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
    )
    adapter.run_one(context, "测试公司")
    assert page.pdf_options["scale"] == 0.35
    assert page.pdf_options["display_header_footer"] is True
    assert context.artifacts == [tmp_path / "cnabs.pdf"]
