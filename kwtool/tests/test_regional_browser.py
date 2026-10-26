"""Browser fixtures for real Playwright events, popups, frames and PDF printing."""
import os
import unicodedata
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

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


def test_beijing_ccdi_restores_header_iframe_query_before_each_print(tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            if parsed.path in ("/", "/index_314.html"):
                # Both the homepage and the freshly reloaded result header
                # start with an empty field, as on the official website.
                body = '''<form name="searchform" method="get" action="/was5/web/search" target="_top">
                    <input id="textfield" name="searchword" value=""><input type="submit"></form>'''
            else:
                params = parse_qs(parsed.query)
                number = int(params.get("page", ["1"])[0])
                after = f'<a href="?searchword={SUBJECT}&page=2">2</a>' if number == 1 else ""
                body = f'''<iframe src="/index_314.html"></iframe>共找到 2 条结果
                    <ul class="news_list"><li>纪检查询第 {number} 页</li></ul><div class="page">{after}</div>'''
            route.fulfill(content_type="text/html", body='<!doctype html><meta charset="utf-8">' + body)

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_ccdi"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {}, Event(), lambda _: None, [], subject_kwargs={"注册地": "北京市"})
        rendered_queries = []
        render = run.render_pdf

        def checked_render(page):
            search = page.frame_locator("iframe[src*='index_314.html']").locator("input[name=searchword]")
            rendered_queries.append(search.input_value())
            return render(page)

        run.render_pdf = checked_render
        adapter.run_one(run, SUBJECT)
        assert rendered_queries == [SUBJECT, SUBJECT]
        pdf = PdfReader(run.artifacts[0])
        assert len(pdf.pages) == 2
        for page in pdf.pages:
            assert SUBJECT in unicodedata.normalize("NFKC", page.extract_text())
        browser.close()


def test_beijing_samr_no_results_exports_one_page_and_finishes(tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            if parsed.path == "/":
                body = '''<form id="form1" method="get" action="/so/s" target="_blank">
                    <input id="qt" name="qt"><input type="submit"></form>'''
            else:
                query = parse_qs(parsed.query)["qt"][0]
                body = f'''<input value="{query}"><div class="no-result">
                    很抱歉！未能搜到您想要的内容
                    <p>建议您：</p><p>1）看看输入的文字是否有错误</p>
                    <p>2）去掉可能不必要的词或条件</p></div>'''
            route.fulfill(content_type="text/html", body='<!doctype html><meta charset="utf-8">' + body)

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_samr"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {"max_pages": 3}, Event(), lambda _: None, [], subject_kwargs={"注册地": "北京市"})
        wait = run.wait_until
        run.wait_until = lambda ready, message, **kwargs: wait(ready, message, timeout_seconds=4)
        adapter.run_one(run, SUBJECT)
        assert len(run.artifacts) == 1
        pdf = PdfReader(run.artifacts[0])
        assert len(pdf.pages) == 1
        assert "未能搜到您想要的内容" in unicodedata.normalize("NFKC", pdf.pages[0].extract_text())
        assert len(context.pages) == 1
        browser.close()


def test_guangdong_procuratorate_restores_query_before_each_print(tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            if parsed.path == "/":
                body = '''<form name="searchform" method="get" target="_blank"
                    action="http://jcysearch.jcrb.com/search/search.shtml">
                    <input name="searchWord"><input type="submit"></form>'''
            else:
                number = int(parse_qs(parsed.query).get("page", ["1"])[0])
                after = f'<a href="?searchWord={SUBJECT}&page=2">2</a>' if number == 1 else ""
                body = f'''<input class="js_search_input" value="">共找到 2 条结果
                    <div class="js_basic_result_left"><div class="item">检察查询第 {number} 页</div></div>
                    <div class="listPager">{after}</div>'''
            route.fulfill(content_type="text/html", body='<!doctype html><meta charset="utf-8">' + body)

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_procuratorate"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {"max_pages": 3}, Event(), lambda _: None, [], subject_kwargs={"注册地": "广东省"})
        rendered_queries = []
        render = run.render_pdf

        def checked_render(page):
            rendered_queries.append(page.locator(".js_search_input").input_value())
            return render(page)

        run.render_pdf = checked_render
        adapter.run_one(run, SUBJECT)
        assert rendered_queries == [SUBJECT, SUBJECT]
        pdf = PdfReader(run.artifacts[0])
        assert len(pdf.pages) == 2
        for page in pdf.pages:
            assert SUBJECT in unicodedata.normalize("NFKC", page.extract_text())
        browser.close()


@pytest.mark.parametrize("mode", ["empty", "results", "loading", "wrong-query"])
def test_shanghai_procuratorate_native_result_template(tmp_path: Path, mode):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            if parsed.path == "/":
                body = '''<form action="/shjcysearch.jspx" target="_blank">
                    <input id="searchText1" name="q"><input type="submit"
                    onclick="/* searchAll */"></form>'''
            else:
                number = int(parse_qs(parsed.query).get("page", ["1"])[0])
                query = "其他主体" if mode == "wrong-query" else SUBJECT
                body = f'<input value="{query}"><div>搜索结果 当前查询条件：{query}</div>'
                if mode == "results":
                    body += f'''<section><div><a href="/article{number}.html">检察文章第 {number} 页</a>
                        <p><span>栏目名称：以案说法</span> <span>更新日期：2026.10.26</span></p></div></section>
                        <div>首页 上一页 <a href="?q={SUBJECT}&page=1">1</a>
                        <a href="?q={SUBJECT}&page=2">2</a> 下一页 尾页 共 2 页 共 9 条记录</div>'''
                if mode != "loading":
                    body += '<footer>上海市人民检察院 版权所有</footer>'
            route.fulfill(content_type="text/html", body='<!doctype html><meta charset="utf-8">' + body)

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_procuratorate"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {"max_pages": 3}, Event(), lambda _: None, [], subject_kwargs={"注册地": "上海市"})
        wait = run.wait_until
        run.wait_until = lambda ready, message, **kwargs: wait(ready, message, timeout_seconds=3)
        if mode in ("loading", "wrong-query"):
            with pytest.raises(RuntimeError, match="未能确认检索结果"):
                adapter.run_one(run, SUBJECT)
            assert not run.artifacts
            assert not run.pdf_path.exists()
        else:
            adapter.run_one(run, SUBJECT)
            pdf = PdfReader(run.artifacts[0])
            assert len(pdf.pages) == (2 if mode == "results" else 1)
            for number, page in enumerate(pdf.pages, 1):
                text = unicodedata.normalize("NFKC", page.extract_text())
                assert SUBJECT in text
                if mode == "results":
                    assert f"检察文章第 {number} 页" in text
        browser.close()
