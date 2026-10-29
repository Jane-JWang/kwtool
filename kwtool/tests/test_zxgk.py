import pytest

from kwtool.adapters.base import SiteUnavailable
from kwtool.adapters.zxgk import HOME, ShixinAdapter, ZhixingAdapter


class SearchButton:
    def __init__(self, page):
        self.page = page

    @property
    def last(self):
        return self

    def is_visible(self):
        return self.page.form_visible


class Page:
    def __init__(self, statuses, *, challenge_resolves=False):
        self.url = "about:blank"
        self.statuses = iter(statuses)
        self.visits = []
        self.form_visible = False
        self.challenge_resolves = challenge_resolves

    def goto(self, url, **_kwargs):
        self.visits.append(url)
        self.url = url
        status = next(self.statuses)
        self.form_visible = status == 200 and url != HOME
        return type("Response", (), {"status": status})()

    def get_by_text(self, text, *, exact):
        assert (text, exact) == ("查询", True)
        return SearchButton(self)

    def wait_for_timeout(self, _milliseconds):
        pass


class Context:
    def __init__(self, page):
        self.page = page
        self.messages = []

    def report_status(self, message):
        self.messages.append(message)

    def check_stop(self):
        pass

    def wait_until(self, ready, _message, *, timeout_seconds):
        if timeout_seconds == 20 and self.page.challenge_resolves:
            self.page.form_visible = True
        if not ready():
            raise TimeoutError()


@pytest.mark.parametrize("adapter", [ShixinAdapter(), ZhixingAdapter()])
def test_412_challenge_is_allowed_to_finish_without_interrupting_navigation(adapter):
    page = Page([412], challenge_resolves=True)
    adapter._open_search_page(Context(page))
    assert page.visits == [f"https://zxgk.court.gov.cn{adapter.path}"]


@pytest.mark.parametrize("adapter", [ShixinAdapter(), ZhixingAdapter()])
def test_412_fallback_reenters_through_homepage(adapter):
    page = Page([412, 200, 200])
    adapter._open_search_page(Context(page))
    assert page.visits == [f"https://zxgk.court.gov.cn{adapter.path}", HOME,
                           f"https://zxgk.court.gov.cn{adapter.path}"]


def test_second_412_can_finish_after_homepage_fallback():
    page = Page([412, 200, 412], challenge_resolves=False)
    context = Context(page)

    def wait_until(ready, _message, *, timeout_seconds):
        if timeout_seconds == 60:
            page.form_visible = True
        if not ready():
            raise TimeoutError()

    context.wait_until = wait_until
    ShixinAdapter()._open_search_page(context)
    assert len(page.visits) == 3


def test_http_error_is_reported_after_challenge_fallback():
    page = Page([412, 400])
    with pytest.raises(SiteUnavailable, match="HTTP 400"):
        ShixinAdapter()._open_search_page(Context(page))


def test_search_form_must_be_on_the_requested_page():
    page = Page([])
    page.url = "https://zxgk.court.gov.cn/"
    page.form_visible = True
    assert not ShixinAdapter()._search_ready(page)
