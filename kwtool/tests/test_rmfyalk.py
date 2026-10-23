from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from kwtool.adapters.base import RunContext
from kwtool.adapters.rmfyalk import HOME_URL, RmfyalkAdapter, _is_subject_response, _logged_in


class Locator:
    def __init__(self, text="", visible=True):
        self.text = text
        self.visible = visible
        self.filled = None
        self.clicked = False

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def fill(self, value):
        self.filled = value

    def click(self):
        self.clicked = True


class Page:
    url = HOME_URL

    def __init__(self):
        self.name = Locator()
        self.search = Locator()
        self.button = Locator()

    def goto(self, url, **kwargs):
        assert url == HOME_URL

    def locator(self, selector):
        return {
            "#alk-header .user .user-name": self.name,
            "#alk-search .general-search-text .keyword": self.search,
            "#alk-search .general-search-submit": self.button,
        }[selector]


def test_login_requires_visible_user_name():
    page = Page()
    assert not _logged_in(page)
    page.name.text = "已登录用户"
    page.name.visible = False
    assert not _logged_in(page)
    page.name.visible = True
    assert _logged_in(page)
    page.url = "https://other.example/home.html"
    assert not _logged_in(page)


def test_search_response_must_match_subject():
    def response(terms):
        return SimpleNamespace(
            url="https://rmfyalk.court.gov.cn/cpws_al_api/api/cpwsAl/search",
            request=SimpleNamespace(method="POST", post_data_json={
                "searchParams": {"keyTitle": terms},
            }),
        )

    assert _is_subject_response(response(["测试企业"]), "测试企业")
    assert not _is_subject_response(response(["其他企业"]), "测试企业")


def test_search_waits_for_login(tmp_path: Path):
    page = Page()
    checks = 0
    messages = []

    def wait_until(ready, message, **kwargs):
        nonlocal checks
        checks += 1
        messages.append(message)
        assert kwargs["user_action"] is True
        assert not ready()
        assert page.search.filled is None
        page.name.text = "已登录用户"
        assert ready()
        raise TimeoutError("stop after login gate")

    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=RmfyalkAdapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=messages.append, artifacts=[],
    )
    context.wait_until = wait_until
    with pytest.raises(TimeoutError, match="stop after login gate"):
        RmfyalkAdapter().run_one(context, "测试企业")
    assert checks == 1
    assert not page.button.clicked
    assert any("登录" in message for message in messages)
