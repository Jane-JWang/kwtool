"""Browser fixtures for real Playwright events, popups, frames and PDF printing."""
import os
import unicodedata
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext


pytestmark = pytest.mark.skipif(os.environ.get("KWTOOL_BROWSER_TESTS") != "1",
                                reason="Set KWTOOL_BROWSER_TESTS=1 to run Chrome fixture")
SUBJECT = "中信证券股份有限公司"
RESULT = """<!doctype html><html><head><meta charset="utf-8"><title>地方官网检索</title></head>
<body><div id="results">正在加载</div><script>
fetch('/api/results').then(r => r.json()).then(data => {
  document.querySelector('#results').textContent = '共找到 ' + data.total + ' 条结果';
});</script></body></html>"""


@pytest.mark.parametrize("key,province", [("regional_court", "广东省"), ("regional_samr", "上海市")])
def test_regional_browser_search_and_print(tmp_path: Path, key, province):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def route_request(route):
            path = urlparse(route.request.url).path
            if path == "/api/results":
                route.fulfill(content_type="application/json", body='{"total":0}')
            elif key == "regional_samr" and path == "/":
                route.fulfill(content_type="text/html", body='<iframe src="/2001/"></iframe>')
            elif path == "/2001/":
                route.fulfill(content_type="text/html", body='''<input id="searchword">
                    <input type="button" class="search_submit" onclick="window.open(
                      '/kbym/#search/query=' + encodeURIComponent(document.querySelector('#searchword').value))">''')
            else:
                route.fulfill(content_type="text/html", body=RESULT)

        context.route("**/*", route_request)
        page = context.new_page()
        adapter = registered_adapters()[key]
        run = RunContext(context, page, tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {}, Event(), lambda message: None, [], subject_kwargs={"注册地": province})
        adapter.run_one(run, SUBJECT)
        assert len(context.pages) == 1
        assert len(run.artifacts) == 1
        reader = PdfReader(run.artifacts[0])
        text = unicodedata.normalize("NFKC", reader.pages[0].extract_text())
        assert "共找到 0 条结果" in text
        assert "地方官网检索" in text
        assert "1/1" in text
        assert ("search.gdcourts.gov.cn" if key == "regional_court" else "scjgj.sh.gov.cn") in text
        browser.close()
