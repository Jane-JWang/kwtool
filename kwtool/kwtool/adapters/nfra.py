"""National Financial Regulatory Administration site search."""

from __future__ import annotations

import io
import re
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


SEARCH_URL = "https://www.nfra.gov.cn/cn/view/pages/index/jiansuo.html"
RESULT_COUNT = re.compile(r"共检索到\s*([\d,，]+)\s*篇文章\s*当前第\s*(\d+)\s*页")


class NfraAdapter(SiteAdapter):
    key = "nfra"
    display_name = "国家金融监督管理总局"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1, minimum=1, maximum=1000),)

    def run_one(self, context: RunContext, subject: str) -> None:
        try:
            max_pages = int(context.site_settings.get("max_pages", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("国家金融监督管理总局前 K 页必须是整数") from exc
        if not 1 <= max_pages <= 1000:
            raise ValueError("国家金融监督管理总局前 K 页必须在 1 到 1000 之间")
        context.check_stop()
        page = context.page
        url = SEARCH_URL + "?" + urlencode({"keyWords": subject})
        context.report_status(f"正在检索国家金融监督管理总局：{subject}")
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开国家金融监督管理总局搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"国家金融监督管理总局搜索返回 HTTP {response.status}")
        if not _allowed(page.url, ("nfra.gov.cn",)):
            raise SiteUnavailable(f"国家金融监督管理总局搜索跳转至非预期网站：{page.url}")

        results = page.locator(".jiansuo-right-result")

        def loaded() -> bool:
            if subject not in parse_qs(urlparse(page.url).query).get("keyWords", []):
                return False
            if page.locator("#keywords").input_value() != subject:
                return False
            # The site hides the result-count paragraph when the count is zero.
            # Its empty-state message becomes visible only after Angular receives
            # the search response.
            if results.get_by_text("无搜索结果", exact=True).is_visible():
                return True
            match = RESULT_COUNT.search(results.inner_text(timeout=5000))
            if match is None:
                return False
            count = int(match.group(1).replace(",", "").replace("，", ""))
            return count > 0 and results.locator(".jiansuo-right-result-list:visible").count() > 0

        try:
            context.wait_until(loaded, "等待国家金融监督管理总局检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("nfra-search-timeout", page)
            raise RuntimeError("国家金融监督管理总局未能确认检索结果，未保存 PDF") from exc

        match = RESULT_COUNT.search(results.inner_text(timeout=5000))
        total = int(match.group(1).replace(",", "").replace("，", "")) if match else 0
        pages_to_print = min(max_pages, max(1, (total + 9) // 10))
        pieces: list[bytes] = []

        for number in range(1, pages_to_print + 1):
            context.check_stop()
            page.evaluate("document.fonts.ready")
            page.emulate_media(media="screen")
            page.evaluate("window.scrollTo(0, 0)")
            context.capture_diagnostic("ready-to-print", page)
            context.report_status(f"正在打印国家金融监督管理总局第 {number} 页")
            if pages_to_print == 1:
                saved = context.save_pdf(page)
                context.report_status(f"已保存国家金融监督管理总局前 1 页检索结果：{saved}")
                return
            pieces.append(context.render_pdf(page))
            if number == pages_to_print:
                break

            previous = tuple(results.locator(".jiansuo-right-result-list:visible").all_inner_texts())
            next_button = page.locator('[pager2] a[ng-click="pager.next()"]:visible')
            if next_button.count() == 0:
                raise RuntimeError(f"国家金融监督管理总局第 {number} 页缺少下一页按钮，未保存 PDF")
            next_button.click()

            def next_loaded() -> bool:
                match = RESULT_COUNT.search(results.inner_text(timeout=5000))
                if match is None or int(match.group(2)) != number + 1:
                    return False
                current = tuple(results.locator(".jiansuo-right-result-list:visible").all_inner_texts())
                return bool(current) and current != previous

            try:
                context.wait_until(next_loaded, f"等待国家金融监督管理总局第 {number + 1} 页检索结果", timeout_seconds=60)
            except TimeoutError as exc:
                context.capture_diagnostic(f"nfra-page-{number + 1}-timeout", page)
                raise RuntimeError(f"国家金融监督管理总局第 {number + 1} 页未加载新结果，未保存 PDF") from exc

        context.check_stop()
        writer = PdfWriter()
        for piece in pieces:
            writer.append(PdfReader(io.BytesIO(piece)))
        output = io.BytesIO()
        writer.write(output)
        saved = save_pdf_bytes(output.getvalue(), context.pdf_path)
        context.pdf_path = saved
        context.artifacts.append(saved)
        context.report_status(f"已保存国家金融监督管理总局前 {len(pieces)} 页检索结果：{saved}")
