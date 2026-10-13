"""Zhongdeng unified movable-financing registry, guarantor query."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from playwright.sync_api import Download, Error as PlaywrightError, Page

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings
from kwtool.output import available_path, safe_filename


HOME = "https://www.zhongdengwang.org.cn/out/#/home"
QUERY = "https://www.zhongdengwang.org.cn/out/#/query/guarantor/"
HOST = "www.zhongdengwang.org.cn"
DOWNLOAD_LABEL = "下载本页面所有登记证明文件及附件"


def _site(page: Page) -> bool:
    return urlparse(page.url).hostname == HOST


def _text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=5000)


def _result_ready(page: Page, subject: str) -> bool:
    if not _site(page) or "/query/guarantor/summary" not in page.url:
        return False
    body = _text(page)
    return (subject in body and "查询结果统计" in body
            and "查询到登记" in body and "查询中" not in body)


def _total_rows(text: str) -> int | None:
    match = re.search(r"共\s*(\d+)\s*条记录", text)
    return int(match.group(1)) if match else None


def _find_detail_page(context: RunContext, search_page: Page, existing_pages: set[Page]) -> Page | None:
    candidates = [search_page, *(page for page in context.browser.pages if page not in existing_pages)]
    for candidate in candidates:
        if (not candidate.is_closed() and _site(candidate)
                and "/query/guarantor/register/Result" in candidate.url
                and "登记信息列表" in _text(candidate)):
            return candidate
    return None


def _input_for(page: Page, *labels: str):
    for label in labels:
        field = page.get_by_placeholder(re.compile(label)).first
        if field.count():
            return field
    for label in labels:
        group = page.locator(".el-form-item").filter(has=page.locator(".el-form-item__label").filter(has_text=label)).first
        if group.count():
            field = group.locator("input.el-input__inner").first
            if field.count():
                return field
    raise RuntimeError(f"中登网未找到输入框：{'、'.join(labels)}")


def _safe_extract(archive: Path, destination: Path) -> list[Path]:
    """Extract a site's ZIP without letting member names escape the output folder."""
    extracted: list[Path] = []
    with ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            parts = Path(member.filename.replace("\\", "/")).parts
            if not parts or any(part in {".", ".."} for part in parts):
                continue
            if Path(member.filename).is_absolute() or ":" in parts[0]:
                continue
            target = destination.joinpath(*(safe_filename(part, limit=160) for part in parts))
            target = available_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("xb") as output:
                import shutil
                shutil.copyfileobj(source, output)
            extracted.append(target)
    return extracted


def _save_overall_document(context: RunContext, download: Download, label: str) -> Path:
    """Save a document for the whole query beside its result PDFs."""
    context.check_stop()
    suffix = Path(download.suggested_filename).suffix.lower()
    if not suffix or len(suffix) > 16:
        raise RuntimeError(f"中登网{label}下载文件缺少有效扩展名")
    destination = available_path(context.pdf_path.with_name(
        f"{context.pdf_path.stem}_{label}{suffix}"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(destination)
    if download.failure():
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"中登网{label}下载失败：{download.failure()}")
    context.artifacts.append(destination)
    return destination


class ZhongdengAdapter(SiteAdapter):
    key = "zhongdeng"
    display_name = "中登网"
    optional_kwargs = ("身份证",)
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        page = context.page
        identity = context.subject_kwargs.get("身份证", "").strip()
        query_value = identity or subject
        try:
            response = page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开中登网：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"中登网返回 HTTP {response.status}")

        context.wait_until(
            lambda: _site(page) and ("欢迎" in _text(page) or "退出登录" in _text(page)),
            "请在 Chrome 中登录中登网，登录后将自动进入按担保人查询…",
            timeout_seconds=600, user_action=True,
        )
        page.goto(QUERY, wait_until="domcontentloaded", timeout=60000)
        context.wait_until(
            lambda: _site(page) and "担保人类型" in _text(page) and "查询原因" in _text(page),
            "等待中登网按担保人查询表单…", timeout_seconds=90,
        )

        choice = "个人" if identity else "机构"
        page.get_by_text(choice, exact=True).first.click()
        if identity:
            _input_for(page, "证件号码").fill(identity)
        else:
            _input_for(page, "机构名称").fill(subject)

        page.locator(".el-form-item").filter(has_text="查询原因").locator(".el-select__wrapper").first.click()
        page.get_by_role("option", name="交易前调查").click()

        self._proof_yes(page)
        captcha = _input_for(page, "校验码", "验证码")
        context.wait_until(
            lambda: _result_ready(page, query_value) or
                    (captcha.count() > 0 and bool(captcha.input_value().strip())),
            "请在 Chrome 中填写中登网校验码；填写后程序将提交查询…",
            timeout_seconds=600, user_action=True,
        )
        # The user may submit while entering the code; never send a second query.
        if not _result_ready(page, query_value):
            page.get_by_text("查询", exact=True).last.click()
        self._proof_yes(page)
        def result_or_proof() -> bool:
            self._proof_yes(page)
            return _result_ready(page, query_value)

        context.wait_until(
            result_or_proof,
            "等待中登网查询结果；如校验码错误，请在 Chrome 中重新填写并查询…",
            timeout_seconds=600, user_action=True,
        )
        page.evaluate("document.fonts.ready")
        self._pdf(context, page, "查询结果")

        detail_link = page.get_by_role("link", name="详情").first
        if not detail_link.count():
            # Zero-result queries have no detail route; the summary itself is
            # the complete official result.
            return
        existing_pages = set(context.browser.pages)
        detail_link.click()
        detail = None

        def detail_ready() -> bool:
            nonlocal detail
            detail = _find_detail_page(context, page, existing_pages)
            return detail is not None

        context.wait_until(
            detail_ready,
            "等待中登网登记详情页…", timeout_seconds=90,
        )
        assert detail is not None
        try:
            self._save_detail(context, detail, identity)
        finally:
            if detail is not page and not detail.is_closed():
                detail.close()

    def _save_detail(self, context: RunContext, page: Page, identity: str) -> None:

        # The detail route contains the paginated list. Wait for its count
        # before choosing 30 rows per page; a newly mounted table may be empty.
        context.wait_until(lambda: _total_rows(_text(page)) is not None,
                           "等待登记信息列表加载…", timeout_seconds=90)
        total = _total_rows(_text(page))
        assert total is not None
        self._save_top_documents(context, page, identity)
        if total == 0:
            self._pdf(context, page, "详情_第1页")
            return

        self._set_page_size(page)
        pages = math.ceil(total / 30)
        for number in range(1, pages + 1):
            context.check_stop()
            self._wait_page(context, page, number, total)
            self._pdf(context, page, f"详情_第{number}页")
            self._download_page(context, page, number)
            if number < pages:
                self._next_page(page, number)

    @staticmethod
    def _proof_yes(page: Page) -> None:
        group = page.locator(".el-form-item").filter(has_text="是否需要查询证明").first
        if group.count() and group.is_visible():
            yes = group.locator(".el-radio").filter(has_text="是").first
            if yes.count() and "is-checked" not in (yes.get_attribute("class") or ""):
                yes.click()

    @staticmethod
    def _set_page_size(page: Page) -> None:
        if re.search(r"每页显示\s*30\s*条记录", _text(page)):
            return
        select = page.locator("select:visible").last
        if select.count():
            try:
                select.select_option(label="30")
                return
            except PlaywrightError:
                pass
        # Ant Design / Element style dropdown.
        trigger = page.locator(".el-select").last
        if not trigger.count():
            raise RuntimeError("中登网未找到每页显示条数控件，无法确认按 30 条分页")
        trigger.click()
        page.get_by_role("option", name="30").click()

    @staticmethod
    def _wait_page(context: RunContext, page: Page, number: int, total: int) -> None:
        expected = min(total, number * 30)
        first = (number - 1) * 30 + 1
        context.wait_until(
            lambda: bool(re.search(rf"显示第\s*{first}\s*到\s*{expected}\s*条记录", _text(page))),
            f"等待中登网第 {number} 页登记记录…", timeout_seconds=60,
        )

    @staticmethod
    def _next_page(page: Page, number: int) -> None:
        page.get_by_text(str(number + 1), exact=True).last.click()

    @staticmethod
    def _pdf(context: RunContext, page: Page, suffix: str) -> Path:
        original = context.pdf_path
        context.pdf_path = original.with_name(f"{original.stem}_{suffix}.pdf")
        try:
            page.emulate_media(media="screen")
            page.evaluate("window.scrollTo(0, 0)")
            return context.save_pdf(page)
        finally:
            context.pdf_path = original

    @staticmethod
    def _download_page(context: RunContext, page: Page, number: int) -> None:
        button = page.get_by_text(DOWNLOAD_LABEL, exact=True).first
        if not button.count() or not button.is_visible():
            raise RuntimeError(f"第 {number} 页未找到登记证明及附件下载按钮")
        with page.expect_download(timeout=120000) as pending:
            button.click()
        download = pending.value
        folder = context.pdf_path.with_name(f"{context.pdf_path.stem}_附件")
        folder.mkdir(parents=True, exist_ok=True)
        archive = available_path(folder / f"第{number}页.zip")
        download.save_as(archive)
        if download.failure():
            archive.unlink(missing_ok=True)
            raise RuntimeError(f"中登网第 {number} 页附件下载失败：{download.failure()}")
        try:
            extracted = _safe_extract(archive, folder)
        except BadZipFile as exc:
            raise RuntimeError(f"第 {number} 页下载的附件不是有效 ZIP：{archive}") from exc
        archive.unlink()
        if folder not in context.artifacts:
            context.artifacts.append(folder)
        context.report_status(f"中登网第 {number} 页已解压 {len(extracted)} 个附件到：{folder}")

    @staticmethod
    def _save_top_documents(context: RunContext, page: Page, identity: str) -> None:
        # The query certificate number is a direct PDF download. It appears
        # for both institution and individual queries when proof was selected.
        certificate = page.locator("a.el-link").filter(has_text=re.compile(r"^\d{15,}$")).first
        if certificate.count():
            with page.expect_download(timeout=60000) as pending:
                certificate.click()
            saved = _save_overall_document(context, pending.value, "查询证明")
            context.report_status(f"中登网查询证明文件已保存：{saved}")

        if identity:
            return
        register_list = page.locator("a.el-link").filter(has_text="登记信息列表").first
        if not register_list.count():
            return
        register_list.click()
        context.wait_until(
            lambda: "下载申请已提交" in _text(page),
            "等待中登网登记信息列表下载申请…", timeout_seconds=60,
        )
        message = page.locator(".el-message-box").inner_text()
        match = re.search(r"下载文件编号[：:]\s*(\d+)", message)
        if not match:
            raise RuntimeError("中登网登记信息列表下载申请未返回文件编号")
        file_number = match.group(1)
        page.get_by_role("button", name="去查看").click()
        context.wait_until(lambda: "/download/" in page.url,
                           "等待中登网下载中心…", timeout_seconds=60)
        field = page.locator(".el-form-item").filter(has_text="下载文件编号").locator("input.el-input__inner").first
        context.wait_until(lambda: field.count() > 0,
                           "等待下载中心查询框…", timeout_seconds=60)
        field.fill(file_number)

        next_refresh = 0.0

        def completed_row():
            nonlocal next_refresh
            if time.monotonic() >= next_refresh:
                page.get_by_role("button", name="查询", exact=True).click()
                next_refresh = time.monotonic() + 5
            row = page.locator("table tbody tr").filter(has_text=file_number).first
            if not row.count():
                return None
            status = row.inner_text()
            if "失败" in status:
                raise RuntimeError(f"中登网登记信息列表生成失败：{file_number}")
            return row if "完成" in status else None

        row = None

        def ready() -> bool:
            nonlocal row
            row = completed_row()
            return row is not None

        context.wait_until(ready, "等待登记信息列表 Excel 生成…", timeout_seconds=300)
        assert row is not None
        with page.expect_download(timeout=60000) as pending:
            row.get_by_text("下载", exact=True).click()
        saved = _save_overall_document(context, pending.value, "登记信息列表")
        context.report_status(f"中登网登记信息列表已保存：{saved}")
        page.go_back(wait_until="domcontentloaded", timeout=60000)
        context.wait_until(lambda: "/query/guarantor/register/Result" in page.url
                           and "登记信息列表" in _text(page),
                           "返回中登网登记详情…", timeout_seconds=90)
