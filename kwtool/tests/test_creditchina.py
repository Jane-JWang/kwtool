"""Local browser fixture; no request is sent to Credit China."""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

import pytest
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext
from kwtool.adapters.creditchina import CATEGORIES, CATEGORY_TABS, CreditChinaAdapter


def test_creditchina_defaults() -> None:
    adapter = CreditChinaAdapter()
    assert adapter.default_pdf_settings.scale == 0.35
    assert [spec.label for spec in adapter.settings_schema] == list(CATEGORIES)
    assert [spec.label for spec in adapter.settings_schema if spec.default] == ["行政管理", "严重失信", "经营异常"]


@pytest.mark.skipif(os.environ.get("KWTOOL_BROWSER_TESTS") != "1", reason="Set KWTOOL_BROWSER_TESTS=1 to run Chrome fixture")
@pytest.mark.parametrize("subject, manual_choice", [("测试企业有限公司", False), ("测试企业", True)])
def test_creditchina_search_pagination_merge_and_report(tmp_path: Path, subject: str, manual_choice: bool) -> None:
    from playwright.sync_api import sync_playwright

    report = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.write(report)
    report_url = "data:application/pdf;base64," + base64.b64encode(report.getvalue()).decode()
    home = """<input placeholder='请输入主体名称或者统一社会信用代码'>
    <div class='new_top_search'><button class='search_btn' onclick="window.open('/xinyongxinxi/index.html')">搜 索</button></div>"""
    search = """<div class='vcodepop'>请输入验证码</div><ul id='companylists'></ul>
    <div class='History_column_list'><a href='/wrong'>测试企业有限公司</a></div>
    <script>setTimeout(() => {
      document.querySelector('.vcodepop').remove();
      document.querySelector('#companylists').innerHTML = `
      <li class='company-item' onclick="window.open('/xinyongxinxixiangqing/xyDetail.html')">
        <p class='company-name'>测试企业有限公司</p><p>统一社会信用代码：123456789</p></li>
      <li class='company-item'><p class='company-name'>测试企业分公司</p></li>`;
    }, 1200)</script>"""
    if manual_choice:
        search += "<script>setTimeout(() => document.querySelector('.company-item')?.click(), 6500)</script>"
    tiles = "".join(f"<li id='{CATEGORY_TABS[index][1:]}' onclick=\"show('{name}')\"><span>{name}</span>"
                    f"<em>{2 if index == 0 else 0}</em></li>" for index, name in enumerate(CATEGORIES))
    detail = f"""<!doctype html><html><head><title>测试企业有限公司 - 信用中国</title>
    <style>.nodata {{ width: 200px; height: 200px; }}</style></head><body>
    <h1>测试企业有限公司</h1><div>统一社会信用代码：123456789</div>
    <ul class='tab-title'>{tiles}</ul><div id='records' class='result-table'></div>
    <button class='layui-laypage-next' onclick='next()'>下一页</button>
    <a download='report.pdf' href='{report_url}'>下载信用信息报告</a>
    <script>
    let section = '', number = 1;
    function render() {{
      if (section === '行政管理')
        document.querySelector('#records').textContent = section + '第' + number + '页';
      else
        document.querySelector('#records').innerHTML = '<div class="nodata"></div>';
      document.querySelector('.layui-laypage-next').classList.toggle('layui-disabled', section !== '行政管理' || number === 2);
    }}
    function show(name) {{
      section = name; number = 1;
      document.querySelectorAll('.tab-title li').forEach(e => e.classList.toggle('cur', e.innerText.includes(name)));
      if (name === '严重失信') {{
        const challenge = document.createElement('div');
        challenge.className = 'vcodepop'; challenge.textContent = '请输入验证码';
        document.body.append(challenge);
        setTimeout(() => {{ challenge.remove(); render(); }}, 1500);
      }} else {{
        document.querySelector('#records').textContent = '';
        if (name === '行政管理') setTimeout(render, 3000); else render();
      }}
    }}
    function next() {{ number++; render(); }}
    show('行政管理');
    </script></body></html>"""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except OSError:
            pytest.skip("Chrome 不可用")
        try:
            browser_context = browser.new_context(accept_downloads=True)
            browser_context.route("https://www.creditchina.gov.cn/**", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8",
                body={"/": home, "/xinyongxinxi/index.html": search,
                      "/xinyongxinxixiangqing/xyDetail.html": detail}[urlparse(route.request.url).path]))
            page = browser_context.new_page()
            adapter = CreditChinaAdapter()
            artifacts: list[Path] = []
            messages: list[str] = []
            context = RunContext(browser_context, page, tmp_path / "测试企业.pdf", adapter.default_pdf_settings,
                                 {}, Event(), messages.append, artifacts)
            adapter.run_one(context, subject)
            website = tmp_path / "测试企业_网站.pdf"
            downloaded = tmp_path / "测试企业_导出报告.pdf"
            assert artifacts == [website, downloaded]
            assert len(PdfReader(website).pages) == 4
            assert len(PdfReader(downloaded).pages) == 1
            assert any("请在右侧 Chrome 的结果列表点击正确企业" in message for message in messages) is manual_choice
            assert any("严重失信板块触发验证码" in message for message in messages)
        finally:
            browser.close()
