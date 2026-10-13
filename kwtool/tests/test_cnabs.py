from pathlib import Path
from threading import Event

from kwtool.adapters.base import RunContext
from kwtool.adapters.cnabs import CnabsAdapter, HOME_URL, SEARCH_URL


SUBJECT = "测试公司"


class Locator:
    def __init__(self, page, selector=""):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def count(self):
        if self.selector == "body":
            return 1
        if "tbody tr" in self.selector:
            return 1 if self.page.institution_count else 0
        return 0

    def inner_text(self, **kwargs):
        return self.page.body

    def click(self):
        if "tbody tr" in self.selector:
            self.page.url = "https://www.cn-abs.com/organization.html#/detail/info?organization_id=490"
            self.page.body = "机构简介 基本信息 " + ("详情内容 " * 30)


class FakePage:
    def __init__(self, institution_count):
        self.url = "about:blank"
        self.body = "上次登录：2026-10-13"
        self.institution_count = institution_count
        self.query = ""
        self.printed = False

    def goto(self, url, **kwargs):
        self.url = url
        if "/main/search" in url:
            self.query = SUBJECT
            self.body = f"产品(0) 证券(0) 机构({self.institution_count}) 专家(0)"
        return None

    def evaluate(self, script, *args):
        if "上次登录" in script:
            return True
        return bool(args) and self.query == args[0]

    def locator(self, selector):
        return Locator(self, selector)

    def get_by_text(self, selector):
        return Locator(self, "institution-tab")

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, delay):
        pass

    def emulate_media(self, **kwargs):
        pass


def run(adapter, page, tmp_path):
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
    saved = []
    context.save_pdf = lambda current: saved.append(current) or context.pdf_path
    adapter.run_one(context, SUBJECT)
    return saved


def test_cnabs_requires_login_then_opens_institution_zero_results(tmp_path: Path):
    page = FakePage(institution_count=0)
    adapter = CnabsAdapter()
    assert run(adapter, page, tmp_path) == [page]
    assert page.url == SEARCH_URL.format("%E6%B5%8B%E8%AF%95%E5%85%AC%E5%8F%B8")
    assert page.query == SUBJECT
    assert adapter.default_pdf_settings.scale == 0.35


def test_cnabs_opens_matching_organization_detail(tmp_path: Path):
    page = FakePage(institution_count=1)
    assert run(CnabsAdapter(), page, tmp_path) == [page]
    assert "/organization.html#/detail/info?organization_id=490" in page.url


def test_cnabs_waits_for_login_before_search(tmp_path: Path):
    page = FakePage(institution_count=0)
    page.body = "请登录"
    login_checks = 0

    def evaluate(script, *args):
        nonlocal login_checks
        if "上次登录" in script:
            login_checks += 1
            return login_checks > 1
        return True

    page.evaluate = evaluate
    saved = run(CnabsAdapter(), page, tmp_path)
    assert saved == [page]
    assert login_checks > 1
    assert page.url != HOME_URL
