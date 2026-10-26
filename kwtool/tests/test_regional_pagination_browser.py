import html
import os
from pathlib import Path
from threading import Event
import unicodedata
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext


pytestmark = pytest.mark.skipif(os.environ.get("KWTOOL_BROWSER_TESTS") != "1",
                                reason="Set KWTOOL_BROWSER_TESTS=1 to run Chrome fixture")
SUBJECT = "测试公司"


def html_page(body):
    return '<!doctype html><html><head><meta charset="utf-8"><title>地方官网检索</title></head><body>' + body + '</body></html>'


@pytest.mark.parametrize("key,province", [("regional_court", "广东省"), ("regional_samr", "上海市")])
@pytest.mark.parametrize("limit,expected", [(1, 1), (3, 2)])
def test_page_limit_last_page_and_pdf_merge(tmp_path: Path, key, province, limit, expected):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            number = int(parse_qs(parsed.query).get("page", ["1"])[0])
            if key == "regional_court":
                after = f'<a href="?keywords={SUBJECT}&page=2">2</a>' if number == 1 else '<span class="disabled">下一页</span>'
                body = f'共找到 2 条结果<div id="list-body"><div class="list-item">文章第 {number} 页</div></div><div id="page-list">{after}</div>'
            elif parsed.path == "/":
                body = '<iframe src="/2001/"></iframe>'
            elif parsed.path == "/2001/":
                body = '<input id="searchword"><input type="button" class="search_submit" onclick="window.open(\'/kbym/#search/query=\'+encodeURIComponent(document.querySelector(\'#searchword\').value))">'
            else:
                # JS-only numbered spans, matching the Shanghai SAMR pager.
                body = '''共找到 2 条结果<div class="maya-result-item">文章第 1 页</div>
                    <ul class="pagination"><li><span v="2" onclick="setTimeout(() => {
                    document.querySelector('.maya-result-item').textContent='文章第 2 页';
                    document.querySelector('.pagination').innerHTML='<li class=disabled><span title=下一页>»</span></li>';
                    }, 300)">2</span></li></ul>'''
            route.fulfill(content_type="text/html", body=html_page(body))

        context.route("**/*", serve)
        adapter = registered_adapters()[key]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {"max_pages": limit}, Event(), lambda _: None, [], subject_kwargs={"注册地": province})
        adapter.run_one(run, SUBJECT)
        assert len(run.artifacts) == 1
        reader = PdfReader(run.artifacts[0])
        assert len(reader.pages) == expected
        for number, page in enumerate(reader.pages, 1):
            assert f"文章第 {number} 页" in unicodedata.normalize("NFKC", page.extract_text())
        browser.close()


def test_shanghai_court_prints_full_parent_and_paginates_iframe(tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        submitted = []

        def serve(route):
            if urlparse(route.request.url).path.endswith("search_zh.jsp"):
                body = '''<h1>上海市高级人民法院</h1><p>查询条件</p>
                <input name="fbrqks" value="2026-08-26"><input name="fbrqjs" value="2026-10-26">
                内容：<input name="nr"><button onclick="do_search()">检索</button>
                <iframe id="search_re" name="search" src="search_re.jsp" style="width:100%;height:400px"></iframe>
                <script>window.mini={getbyName: name=>({setValue: value=>document.querySelector('[name='+name+']').value=value})};
                function do_search(){const frame=window.frames.search;const form=frame.document.forms.pageForm;
                  for (const name of ['nr','fbrqks','fbrqjs']) form.elements[name].value=document.querySelector('[name='+name+']').value;
                  form.submit();}</script>'''
            else:
                values = parse_qs(route.request.post_data or "", keep_blank_values=True)
                submitted.append(values)
                number = int(values.get("pageNum", ["1"])[0])
                subject = values.get("nr", [""])[0]
                after = '<a href="javascript:;" onclick="document.forms.pageForm.pageNum.value=2;document.forms.pageForm.submit()">2</a>' if number == 1 and subject else ""
                article = f'<a class="esty_a" href="xxnr.jsp?id={number}">案例第 {number} 页</a>' if subject else '暂时没有信息'
                body = f'''检索结果<form name="pageForm" method="post" action="search_re.jsp">
                    <input name="nr" type="hidden" value="{html.escape(subject)}">
                    <input name="fbrqks" type="hidden"><input name="fbrqjs" type="hidden">
                    <input name="pageNum" type="hidden" value="{number}">{article}{after}</form>'''
            route.fulfill(content_type="text/html", body=html_page(body))

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_court"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {}, Event(), lambda _: None, [], subject_kwargs={"注册地": "上海市"})
        adapter.run_one(run, SUBJECT)
        assert all(v.get("nr") == [SUBJECT] for v in submitted if v)
        assert submitted[-1]["fbrqks"] == [""]
        reader = PdfReader(run.artifacts[0])
        assert len(reader.pages) == 2
        for number, page in enumerate(reader.pages, 1):
            text = unicodedata.normalize("NFKC", page.extract_text())
            assert "上海市高级人民法院" in text
            assert "查询条件" in text
            assert SUBJECT in text
            assert f"案例第 {number} 页" in text
            assert "search_zh.jsp" in text
        browser.close()


def test_beijing_court_preserves_keyword_when_turning_pages(tmp_path: Path):
    submitted = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()

        def serve(route):
            parsed = urlparse(route.request.url)
            if parsed.path == "/":
                body = '<form id="article_content" method="post" action="/article/essearch.shtml"><input id="keyword" name="keyword"><input type="submit"></form>'
            else:
                values = parse_qs(route.request.post_data or "")
                submitted.append(values)
                number = int(values.get("page", ["1"])[0])
                if values.get("keyword") != [SUBJECT]:
                    body = "关键词不能为空"
                else:
                    after = '<a class="page-link" data-page="2" href="javascript:;">2</a>' if number == 1 else ""
                    body = f'''共找到 2 条结果<input name="keyword" value="">
                        <div class="search_content"><ul><li>北京案例第 {number} 页</li></ul></div>
                        <div class="paginationControl">{after}</div>'''
            route.fulfill(content_type="text/html", body=html_page(body))

        context.route("**/*", serve)
        adapter = registered_adapters()["regional_court"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {}, Event(), lambda _: None, [], subject_kwargs={"注册地": "北京市"})
        adapter.run_one(run, SUBJECT)
        assert len(submitted) == 2
        assert all(values["keyword"] == [SUBJECT] for values in submitted)
        reader = PdfReader(run.artifacts[0])
        assert len(reader.pages) == 2
        assert "北京案例第 2 页" in reader.pages[1].extract_text()
        browser.close()


def test_unchanged_articles_after_page_click_do_not_create_pdf(tmp_path: Path):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html_page(
            f'共找到 2 条结果<div id="list-body"><div class="list-item">相同文章</div></div>'
            f'<div id="page-list"><a href="?keywords={SUBJECT}&page=2">2</a></div>')))
        adapter = registered_adapters()["regional_court"]
        run = RunContext(context, context.new_page(), tmp_path / "result.pdf", adapter.default_pdf_settings,
                         {}, Event(), lambda _: None, [], subject_kwargs={"注册地": "广东省"})
        wait = run.wait_until
        run.wait_until = lambda ready, message, **kwargs: wait(ready, message, timeout_seconds=2)
        with pytest.raises(RuntimeError, match="未能确认检索结果"):
            adapter.run_one(run, SUBJECT)
        assert not run.artifacts
        assert not run.pdf_path.exists()
        browser.close()
