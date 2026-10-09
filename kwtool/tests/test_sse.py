"""Shanghai Stock Exchange search and PDF export tests."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import pytest
from pypdf import PdfReader

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.sse import DEFAULT_KEYWORDS, SECTIONS, SseAdapter, parse_keywords, search_url


def test_sse_configuration_and_url() -> None:
    adapter = registered_adapters()["sse"]
    assert isinstance(adapter, SseAdapter)
    assert adapter.default_pdf_settings.scale == 0.35
    assert adapter.default_pdf_settings.print_background is True
    assert adapter.settings_schema[0].choices == ("精确搜索", "模糊搜索")
    assert adapter.settings_schema[1].default == DEFAULT_KEYWORDS
    assert adapter.settings_schema[2].default == 1
    assert [spec.label for spec in adapter.settings_schema[3:]] == [name for name, _ in SECTIONS]
    assert [spec.default for spec in adapter.settings_schema[3:]] == [True, False, False, False, False]
    assert parse_keywords("  行政处罚   警示函\t责令改正  行政处罚  ") == ("行政处罚", "警示函", "责令改正")
    parsed = urlparse(search_url("中信建投证券股份有限公司 & 甲"))
    assert parsed.hostname == "www.sse.com.cn"
    assert parsed.path == "/home/search/index.shtml"
    assert parse_qs(parsed.query) == {"webswd": ["中信建投证券股份有限公司 & 甲"]}
    assert "%20" in parsed.query and "+" not in parsed.query


def test_sse_requires_a_section(tmp_path: Path) -> None:
    adapter = SseAdapter()
    context = RunContext(None, None, tmp_path / "result.pdf", adapter.default_pdf_settings,  # type: ignore[arg-type]
                         {f"section_{i}": False for i in range(len(SECTIONS))},
                         Event(), lambda _: None, [])
    with pytest.raises(ValueError, match="至少勾选一个"):
        adapter.run_one(context, "测试公司")


@pytest.mark.skipif(os.environ.get("KWTOOL_BROWSER_TESTS") != "1", reason="Set KWTOOL_BROWSER_TESTS=1 to run Chrome fixture")
def test_sse_prints_selected_sections_and_merges_in_order(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    tabs = "".join(f'<span index="{tab}" class="{"active" if i == 0 else ""}" onclick="choose({i})">{name}</span>'
                   for i, (name, tab) in enumerate(SECTIONS))
    panels = "".join(f'''<section id="panel-{i}" style="display:{"block" if i == 0 else "none"}">
        <div class="tool_title" onclick="setMode({i}, 0)">精确搜索<span>按时间排序</span></div>
        <div class="tool_title titleSelected" onclick="setMode({i}, 1)">模糊搜索<span>按相关度排序</span></div>
        <div class="list-group-item"><a class="bot_title_a">SECTION{i}-PAGE1</a></div>
        <div class="pagination-box"><ul><li class="active"><a>1</a></li>
          <li class="next"><a href="javascript:sseSearch.getAllWordsResultNew(1,20);"
            onclick="nextPage({i}); return false;">下一页</a></li></ul></div>
        </section>''' for i in range(len(SECTIONS)))
    html = f'''<!doctype html><html><head><title>上证搜索 | 上海证券交易所</title>
      <style>body {{font-size:14px}} .tool_title {{display:inline-block;padding:8px}}</style></head><body>
      <input id="searchword"><div id="tab-switch0">{tabs}</div>{panels}
      <script>
      document.querySelector('#searchword').value = new URLSearchParams(location.search).get('webswd');
      const keyword = document.querySelector('#searchword').value.split(' ').at(-1);
      document.querySelectorAll('.bot_title_a').forEach(x=>x.textContent += '-' + keyword);
      function choose(i) {{
        document.querySelectorAll('#tab-switch0 span').forEach((x,j)=>x.classList.toggle('active',j===i));
        document.querySelectorAll('section').forEach((x,j)=>x.style.display=j===i?'block':'none');
      }}
      function nextPage(i) {{
        document.querySelector('#panel-'+i+' .bot_title_a').textContent='SECTION'+i+'-PAGE2-'+keyword;
        document.querySelector('#panel-'+i+' .pagination-box li.active a').textContent='2';
        document.querySelector('#panel-'+i+' .pagination-box li.next a').href='javascript:void(0);';
      }}
      function setMode(i, n) {{
        document.querySelectorAll('#panel-'+i+' .tool_title').forEach((x,j)=>x.classList.toggle('titleSelected',j===n));
      }}
      </script></body></html>'''

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except OSError:
            pytest.skip("Chrome 不可用")
        try:
            browser_context = browser.new_context()
            browser_context.route("https://www.sse.com.cn/home/search/**", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=html))
            adapter = SseAdapter()
            context = RunContext(browser_context, browser_context.new_page(), tmp_path / "result.pdf",
                                 adapter.default_pdf_settings,
                                 {"search_mode": "精确搜索", "keywords": "alpha   beta alpha", "max_pages": 2,
                                  "section_0": True, "section_1": False,
                                  "section_2": False, "section_3": True, "section_4": False},
                                 Event(), lambda _: None, [])
            adapter.run_one(context, "测试公司")
            assert context.artifacts == [tmp_path / "result.pdf"]
            pages = PdfReader(context.artifacts[0]).pages
            assert len(pages) == 8
            assert "SECTION0-PAGE1-alpha" in pages[0].extract_text()
            assert "SECTION0-PAGE2-alpha" in pages[1].extract_text()
            assert "SECTION3-PAGE1-alpha" in pages[2].extract_text()
            assert "SECTION3-PAGE2-alpha" in pages[3].extract_text()
            assert "SECTION0-PAGE1-beta" in pages[4].extract_text()
            assert "SECTION0-PAGE2-beta" in pages[5].extract_text()
            assert "SECTION3-PAGE1-beta" in pages[6].extract_text()
            assert "SECTION3-PAGE2-beta" in pages[7].extract_text()
            assert "SECTION3" not in pages[0].extract_text()
        finally:
            browser.close()
