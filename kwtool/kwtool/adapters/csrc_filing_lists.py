"""CSRC filing rosters published under the filing results channel.

The source data is an Office attachment, so keep that original next to a
browser-print PDF of the official announcement.  The roster is not a search
service: the subject is used to name the output, not to assert a match.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


LIST_URL = "https://www.csrc.gov.cn/csrc/c100102/common_list.shtml?channelid=266a63ac0abb4bf49fdfb8598c502bae"
RATING_TITLE = "完成首次备案的证券评级机构名录"
ACCOUNTING_TITLE = "从事证券服务业务会计师事务所备案名录"


def _official_url(url: str) -> bool:
    return urlparse(url).hostname == "www.csrc.gov.cn"


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def roster_match(path: Path, subject: str) -> bool | None:
    """Return true/false for readable Office XML, otherwise unknown.

    A negative result is meaningful only when the expected document XML was
    present and contained text. Legacy XLS and malformed files are unknown.
    """
    suffix = path.suffix.lower()
    if suffix not in {".docx", ".xlsx"}:
        return None
    try:
        with ZipFile(path) as archive:
            if suffix == ".docx":
                if "word/document.xml" not in archive.namelist():
                    return None
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                units = [
                    "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
                    for paragraph in root.iter() if paragraph.tag.endswith("}p")
                ]
            else:
                names = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
                if not names:
                    return None
                shared: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                    shared = [
                        "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
                        for item in strings if item.tag.endswith("}si")
                    ]
                units = []
                for name in names:
                    sheet = ElementTree.fromstring(archive.read(name))
                    for cell in sheet.iter():
                        if not cell.tag.endswith("}c"):
                            continue
                        if cell.attrib.get("t") == "s":
                            value = next((node.text for node in cell if node.tag.endswith("}v")), None)
                            if value is not None:
                                units.append(shared[int(value)])
                        else:
                            units.append("".join(
                                node.text or "" for node in cell.iter()
                                if node.tag.endswith("}t") or node.tag.endswith("}v")
                            ))
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError, ValueError, IndexError):
        return None
    units = [_normalized(unit) for unit in units if _normalized(unit)]
    needle = _normalized(subject)
    if not units or not needle:
        return None
    return any(needle in unit for unit in units)


class _CsrcFilingListAdapter(SiteAdapter):
    title: str
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status(f"正在查找证监会最新公告：{self.title}")
        try:
            response = page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开证监会备案结果公示栏目：{exc}") from exc
        if not _official_url(page.url) or (response is not None and response.status >= 400):
            raise SiteUnavailable(f"证监会备案结果公示栏目不可用：{page.url}")

        # The first matching item in the newest-first listing is the current
        # roster. Do not accidentally pick similarly named cancellation lists.
        link = page.locator("a").filter(has_text=self.title).first
        if link.count() == 0:
            raise SiteUnavailable(f"证监会备案结果公示首页未找到：{self.title}")
        href = link.get_attribute("href")
        article_url = urljoin(page.url, href or "")
        if not _official_url(article_url):
            raise SiteUnavailable(f"证监会公告链接域名异常：{article_url}")
        try:
            response = page.goto(article_url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开证监会名录公告：{exc}") from exc
        if not _official_url(page.url) or (response is not None and response.status >= 400):
            raise SiteUnavailable(f"证监会名录公告不可用：{page.url}")
        context.wait_until(
            lambda: self.title in page.locator("body").inner_text(timeout=5000),
            f"等待证监会名录公告加载：{self.title}", timeout_seconds=30,
        )

        # The two official announcements carry their roster as DOCX / XLSX.
        # Save the original before printing; it contains the actual names,
        # whereas the HTML page only lists an attachment link.
        attachments = page.locator("a[href$='.docx'], a[href$='.xlsx'], a[href$='.xls']")
        if attachments.count() == 0:
            raise SiteUnavailable(f"证监会名录公告没有可下载的名单附件：{page.url}")
        attachment = attachments.first
        attachment_url = urljoin(page.url, attachment.get_attribute("href") or "")
        if not _official_url(attachment_url):
            raise SiteUnavailable(f"证监会名单附件链接域名异常：{attachment_url}")
        with page.expect_download(timeout=60000) as download_info:
            attachment.click()
        saved_attachment = context.save_download(download_info.value)
        context.report_status(f"已保存证监会名单原件：{saved_attachment}")

        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        saved_pdf = context.save_pdf(page)
        context.report_status(f"已保存证监会名录公告：{saved_pdf}")


class CsrcRatingFilingAdapter(_CsrcFilingListAdapter):
    key = "csrc_rating_filing"
    display_name = RATING_TITLE
    title = RATING_TITLE


class CsrcAccountingFilingAdapter(_CsrcFilingListAdapter):
    key = "csrc_accounting_filing"
    display_name = ACCOUNTING_TITLE
    title = ACCOUNTING_TITLE
