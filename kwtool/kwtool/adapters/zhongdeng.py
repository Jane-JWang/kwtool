"""Zhongdeng unified movable-financing registry, guarantor query."""

from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from playwright.sync_api import Error as PlaywrightError, Page

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
    return subject in body and "查询结果统计" in body and "登记信息列表" in body


def _total_rows(text: str) -> int | None:
    match = re.search(r"共\s*(\d+)\s*条记录", text)
    return int(match.group(1)) if match else None


def _input_for(page: Page, *labels: str):
    for label in labels:
        field = page.get_by_placeholder(re.compile(label)).first
        if field.count():
            return field
    for label in labels:
        caption = page.get_by_text(label, exact=True).first
        if caption.count():
            field = caption.locator("xpath=following::input[1]")
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


class ZhongdengAdapter(SiteAdapter):
    key = "zhongdeng"
    display_name = "中登网"
    optional_kwargs = ("身份证",)
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        page = context.page
        identity = context.subject_kwargs.get("身份证", "").strip()
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
            lambda: _site(page) and "机构名称" in _text(page) and "查询原因" in _text(page),
            "等待中登网按担保人查询表单…", timeout_seconds=90,
        )

        choice = "个人" if identity else "机构"
        page.get_by_text(choice, exact=True).first.click()
        _input_for(page, *("担保人姓名", "姓名") if identity else ("机构名称",)).fill(subject)
        if identity:
            _input_for(page, "身份证", "证件号码", "证件号").fill(identity)

        reason = page.locator("select:visible").first
        if reason.count():
            reason.select_option(label="交易前调查")
        else:
            page.get_by_text(re.compile("请选择.*查询原因|交易前调查")).first.click()
            page.get_by_text("交易前调查", exact=True).last.click()

        self._proof_yes(page)
        captcha = _input_for(page, "校验码", "验证码")
        context.wait_until(
            lambda: _result_ready(page, subject) or
                    (captcha.count() > 0 and bool(captcha.input_value().strip())),
            "请在 Chrome 中填写中登网校验码；填写后程序将提交查询…",
            timeout_seconds=600, user_action=True,
        )
        # The user may submit while entering the code; never send a second query.
        if not _result_ready(page, subject):
            page.get_by_text("查询", exact=True).last.click()
        self._proof_yes(page)
        def result_or_proof() -> bool:
            self._proof_yes(page)
            return _result_ready(page, subject)

        context.wait_until(
            result_or_proof,
            "等待中登网查询结果；如校验码错误，请在 Chrome 中重新填写并查询…",
            timeout_seconds=600, user_action=True,
        )
        page.evaluate("document.fonts.ready")
        self._pdf(context, page, "查询结果")

        # The summary route also contains the detail list. Wait for its count
        # before choosing 30 rows per page; a newly mounted table may be empty.
        context.wait_until(lambda: _total_rows(_text(page)) is not None,
                           "等待登记信息列表加载…", timeout_seconds=90)
        total = _total_rows(_text(page))
        assert total is not None
        self._save_optional_list(context, page, identity)
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
        # A separate confirmation can appear before or after submitting the
        # form. When the choice is present, explicitly select Yes.
        for label in ("是否需要查询证明", "需要查询证明"):
            field = page.get_by_text(label, exact=False).first
            if field.count() and field.is_visible():
                yes = page.get_by_text("是", exact=True).first
                if yes.count() and yes.is_visible():
                    yes.click()
                return

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
        trigger = page.locator(".el-pagination__sizes, .ant-pagination-options-size-changer, .ivu-page-options-sizer").first
        if not trigger.count():
            raise RuntimeError("中登网未找到每页显示条数控件，无法确认按 30 条分页")
        trigger.click()
        page.get_by_text("30", exact=True).last.click()

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
        archive = context.save_download(pending.value)
        if archive.suffix.lower() == ".zip":
            folder = archive.parent / f"第{number}页_解压"
            try:
                extracted = _safe_extract(archive, folder)
            except BadZipFile as exc:
                raise RuntimeError(f"第 {number} 页下载的附件不是有效 ZIP：{archive}") from exc
            context.artifacts.extend(extracted)
        context.report_status(f"中登网第 {number} 页附件已保存：{archive}")

    @staticmethod
    def _save_optional_list(context: RunContext, page: Page, identity: str) -> None:
        label = "查询证明" if identity else "登记信息列表"
        link = page.locator("a").filter(has_text=label).last
        if not link.count() or not link.is_visible():
            return
        try:
            with page.expect_download(timeout=20000) as pending:
                link.click()
            context.save_download(pending.value)
        except PlaywrightError:
            # Some deployments expose this as a plain section heading. The
            # per-page document download below remains the source of records.
            pass
