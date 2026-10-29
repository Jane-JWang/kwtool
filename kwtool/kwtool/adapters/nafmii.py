"""Official NAFMII member rosters and downloadable announcements."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import available_path, safe_filename


ROOT = "https://www.nafmii.org.cn/hyfw/hyflmd/"


@dataclass(frozen=True)
class Roster:
    key: str
    title: str
    path: str
    article: bool = False


ROSTERS = (
    Roster("issuer", "交易商协会发行人会员名单", "hyflmd_701/qy/fxr_1124/"),
    Roster("rating_member", "交易商协会评级机构会员名单", "hyflmd_701/zjjg_1143/pjjg/"),
    Roster("trust", "交易商协会非银行类金融机构信托公司会员名单", "hyflmd_701/fyxljrjg/xtgs/"),
    Roster("underwriter", "非金融企业债务融资工具承销机构名单", "fjrqyzwrzgjcxjgmd/", True),
    Roster("bank", "交易商协会银行类金融机构全国性银行会员名单", "hyflmd_701/yxljrjg/qgxyx/"),
    Roster("rating_usable", "评级结果可以在银行间债券市场使用的评级机构名单", "pjjgmd/", True),
    Roster("law", "交易商协会律师事务所会员名单", "hyflmd_701/zjjg_1143/lssws/"),
    Roster("accounting", "交易商协会会计师事务所会员名单", "hyflmd_701/zjjg_1143/hjssws/"),
)


def _official(url: str) -> bool:
    return urlparse(url).hostname == "www.nafmii.org.cn"


def _navigate(context: RunContext, url: str) -> None:
    try:
        response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightError as exc:
        raise SiteUnavailable(f"无法打开交易商协会页面：{exc}") from exc
    if not _official(context.page.url) or (response is not None and response.status >= 400):
        raise SiteUnavailable(f"交易商协会页面不可用：{context.page.url}")


class NafmiiAdapter(SiteAdapter):
    key = "nafmii"
    display_name = "交易协商会"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = tuple(FieldSpec(item.key, item.title, "bool", False) for item in ROSTERS)

    def run_one(self, context: RunContext, subject: str) -> None:
        chosen = [item for item in ROSTERS if context.site_settings.get(item.key, False)]
        if not chosen:
            raise ValueError("请至少勾选一个交易协商会名单")
        original_path = context.pdf_path
        for item in chosen:
            context.check_stop()
            context.report_status(f"正在打开交易商协会名单：{item.title}")
            context.pdf_path = original_path.with_name(
                f"{original_path.stem}_{safe_filename(item.title)}.pdf"
            )
            _navigate(context, urljoin(ROOT, item.path))
            if item.article:
                link = context.page.locator("a").filter(has_text=item.title).first
                if link.count() == 0:
                    raise SiteUnavailable(f"交易商协会未找到公告：{item.title}")
                article_url = urljoin(context.page.url, link.get_attribute("href") or "")
                if not _official(article_url):
                    raise SiteUnavailable(f"交易商协会公告链接域名异常：{article_url}")
                _navigate(context, article_url)
            else:
                # Member category pages fetch their rows asynchronously. Search
                # the current subject before printing so the PDF shows its result.
                context.wait_until(
                    lambda: context.page.locator(".yh1_hyflmd_ul li").count() > 0,
                    f"等待交易商协会会员名单加载：{item.title}", timeout_seconds=60,
                )
                context.page.locator(".searchWord:visible").first.fill(subject)
                with context.page.expect_response(
                    lambda response: "/yhj/search" in response.url and response.request.method == "POST",
                    timeout=60000,
                ) as result_info:
                    context.page.locator(".searth_btn:visible").first.click()
                result = result_info.value.json()
                if result.get("code") != "100000":
                    raise SiteUnavailable(f"交易商协会名单查询失败：{item.title}")
                if result.get("result", {}).get("items"):
                    context.wait_until(
                        lambda: "符合展示条件的" in context.page.locator(".searchFind").first.inner_text(timeout=5000),
                        f"等待交易商协会名单查询结果：{item.title}", timeout_seconds=60,
                    )
                else:
                    # The site's zero-result branch leaves the previous rows
                    # visible. Clear them before printing to avoid false hits.
                    context.page.evaluate("""() => {
                        document.querySelectorAll('.yh1_hyflmd_ul li').forEach(row => row.remove());
                        const label = document.querySelector('.searchFind');
                        if (label) label.textContent = '符合展示条件的（0家）';
                    }""")
            if item.article:
                context.wait_until(
                    lambda: item.title in context.page.locator("body").inner_text(timeout=5000),
                    f"等待交易商协会名单加载：{item.title}", timeout_seconds=60,
                )
            if item.article:
                attachment = context.page.locator("a[href$='.pdf'], a[href$='.xlsx'], a[href$='.xls'], a[href$='.docx']").filter(has_text=item.title).first
                if attachment.count() == 0:
                    raise SiteUnavailable(f"交易商协会公告缺少名单附件：{item.title}")
                href = urljoin(context.page.url, attachment.get_attribute("href") or "")
                if not _official(href):
                    raise SiteUnavailable(f"交易商协会附件链接域名异常：{href}")
                response = context.page.context.request.get(href, timeout=60000)
                if not response.ok:
                    raise SiteUnavailable(f"交易商协会附件下载失败：HTTP {response.status}")
                suffix = urlparse(href).path.rsplit(".", 1)[-1].lower()
                destination = available_path(context.pdf_path.with_name(
                    f"{context.pdf_path.stem}_附件.{suffix}"
                ))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(response.body())
                context.artifacts.append(destination)
                context.report_status(f"已保存交易商协会名单附件：{destination}")
            context.page.evaluate("document.fonts.ready")
            context.page.emulate_media(media="screen")
            context.page.evaluate("window.scrollTo(0, 0)")
            saved = context.save_pdf(context.page)
            context.report_status(f"已保存交易商协会名单页面：{saved}")
