"""Local browser fixture for the full GSXT adapter flow; no site requests occur."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from kwtool.adapters.base import RunContext
from kwtool.adapters.gsxt import GsxtAdapter


pytestmark = pytest.mark.skipif(os.environ.get("KWTOOL_BROWSER_TESTS") != "1", reason="Set KWTOOL_BROWSER_TESTS=1 to run Chrome fixture")


HOME_HTML = """<!doctype html><html><body>
<input id="keyword"><button id="btn_query" onclick="location.href='/search'">查询</button>
</body></html>"""
SEARCH_HTML = """<!doctype html><html><body>
<a class="search_list_item" href="/detail"><h1>测试企业有限公司</h1></a>
</body></html>"""
CHOICE_HTML = """<!doctype html><html><body>
<a class="search_list_item" href="/wrong"><h1>测试企业甲</h1></a>
<a class="search_list_item" href="/detail"><h1>测试企业有限公司</h1></a>
<script>setTimeout(()=>document.querySelectorAll('.search_list_item')[1].click(), 700)</script>
</body></html>"""
DETAIL_HTML = """<!doctype html><html><head><title>测试企业有限公司</title></head><body>
<button class="result-menu__sub-btn" onclick="document.querySelector('#section1').textContent='栏目一内容'">栏目一</button>
<button class="result-menu__sub-btn" onclick="document.querySelector('#section2').textContent='栏目二内容'">栏目二</button>
<div id="section1"></div><div id="section2"></div>
<button id="moreActionsToggle" onclick="document.querySelector('#btn_send_pdf').style.display='block'">更多</button>
<button id="btn_send_pdf" style="display:none" onclick="document.querySelector('#send_pdf_dialog').style.display='block'">发送报告</button>
<div id="send_pdf_dialog" style="display:none"><button onclick="window.__sends++;document.querySelector('#confirm').style.display='block'">发送</button></div>
<div id="confirm" role="dialog" style="display:none">确认发送报告？<button onclick="document.querySelector('#success').textContent='报告稍后发送，请注意查收';this.parentElement.style.display='none'">确认</button></div>
<div id="success"></div><script>window.__sends=0</script>
</body></html>"""


@pytest.mark.parametrize("subject,search_html,manual_choice", [
    ("测试企业有限公司", SEARCH_HTML, False),
    ("测试企业", CHOICE_HTML, True),
])
def test_gsxt_local_browser_flow(tmp_path: Path, subject: str, search_html: str, manual_choice: bool) -> None:
    playwright = sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except OSError:
            pytest.skip("Chrome 不可用")
        try:
            context = browser.new_context()
            page = context.new_page()

            def fulfill(route) -> None:
                path = urlparse(route.request.url).path
                body = {"/": HOME_HTML, "/search": search_html, "/detail": DETAIL_HTML}.get(path)
                if body is None:
                    route.abort()
                else:
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

            context.route("https://www.gsxt.gov.cn/**", fulfill)
            messages: list[str] = []
            artifacts: list[Path] = []
            adapter = GsxtAdapter()
            run = RunContext(
                browser=context,
                page=page,
                pdf_path=tmp_path / "测试企业.pdf",
                pdf_settings=adapter.default_pdf_settings,
                site_settings={"column_delay_ms": 150},
                stop_event=Event(),
                report_status=messages.append,
                artifacts=artifacts,
            )
            adapter.run_one(run, subject)
            assert artifacts == [tmp_path / "测试企业.pdf"]
            assert artifacts[0].read_bytes().startswith(b"%PDF")
            assert run.page.evaluate("window.__sends") == 1
            assert "栏目一内容" in run.page.locator("body").inner_text()
            assert "栏目二内容" in run.page.locator("body").inner_text()
            assert messages[-1] == "网站已确认发送邮件报告"
            assert any("请在右侧网页选择企业" in message for message in messages) is manual_choice
        finally:
            browser.close()
    finally:
        playwright.stop()
