"""Baidu search results, printed one result page at a time."""

from __future__ import annotations

import io
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError, Page
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


DEFAULT_KEYWORDS = "立案 调查 监管 处罚"


def search_url(query: str, page_number: int) -> str:
    return "https://www.baidu.com/s?" + urlencode({"wd": query, "pn": (page_number - 1) * 10})


def _result_state(page: Page, query: str, page_number: int) -> str:
    parsed = urlparse(page.url)
    params = parse_qs(parsed.query)
    if parsed.hostname not in {"www.baidu.com", "baidu.com"} or parsed.path != "/s":
        return "challenge"
    if params.get("wd", [None])[0] != query or params.get("pn", ["0"])[0] != str((page_number - 1) * 10):
        return "challenge"
    return page.evaluate("""() => {
      const results = document.querySelector('#content_left');
      if (results && results.querySelector('.result, .c-container, [data-click]')) return 'results';
      const text = document.body?.innerText || '';
      if (/没有找到|未找到相关结果|抱歉.*没有/.test(text)) return 'empty';
      return 'loading';
    }""")


class BaiduAdapter(SiteAdapter):
    key = "baidu"
    display_name = "百度"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (
        FieldSpec("keywords", "关键词（空格分隔）", "text", DEFAULT_KEYWORDS),
        FieldSpec("max_pages", "前 K 页", "integer", 2),
    )

    def run_one(self, context: RunContext, subject: str) -> None:
        keywords = tuple(dict.fromkeys(str(context.site_settings.get("keywords", DEFAULT_KEYWORDS)).split()))
        if not keywords:
            raise ValueError("请填写至少一个百度搜索关键词")
        try:
            max_pages = int(context.site_settings.get("max_pages", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("百度前 K 页必须是整数") from exc
        if not 1 <= max_pages <= 100:
            raise ValueError("百度前 K 页必须在 1 到 100 之间")

        pieces: list[bytes] = []
        page = context.page
        for keyword in keywords:
            query = f"{subject} {keyword}"
            for page_number in range(1, max_pages + 1):
                context.check_stop()
                context.report_status(f"正在搜索百度：{query}，第 {page_number} 页")
                try:
                    response = page.goto(search_url(query, page_number), wait_until="domcontentloaded", timeout=60000)
                except PlaywrightError as exc:
                    raise SiteUnavailable(f"无法打开百度搜索页：{exc}") from exc
                if response is not None and response.status >= 500:
                    raise SiteUnavailable(f"百度搜索页返回 HTTP {response.status}")

                state = _result_state(page, query, page_number)
                if state != "results" and state != "empty":
                    context.wait_until(
                        lambda: _result_state(page, query, page_number) in {"results", "empty"},
                        f"百度搜索可能需要验证，请在右侧 Chrome 完成；等待“{query}”第 {page_number} 页…",
                        timeout_seconds=600, user_action=True,
                    )
                    state = _result_state(page, query, page_number)
                if state == "empty" and page_number > 1:
                    break
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="screen")
                page.evaluate("window.scrollTo(0, 0)")
                context.capture_diagnostic(f"baidu-{keyword}-{page_number}-ready-to-print", page)
                pieces.append(context.render_pdf(page))

        context.check_stop()
        writer = PdfWriter()
        for piece in pieces:
            writer.append(PdfReader(io.BytesIO(piece)))
        output = io.BytesIO()
        writer.write(output)
        saved = save_pdf_bytes(output.getvalue(), context.pdf_path)
        context.artifacts.append(saved)
        context.report_status(f"已保存百度搜索结果：{saved}")
