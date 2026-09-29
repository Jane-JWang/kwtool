from __future__ import annotations

from html import escape

from kwtool.adapters.base import RunContext, SiteAdapter
from kwtool.models import FieldSpec


class DemoAdapter(SiteAdapter):
    key = "demo"
    display_name = "本地演示"
    settings_schema = (
        FieldSpec("manual_step", "模拟人工验证", "bool", True),
        FieldSpec("page_title", "页面标题", "text", "网络核查演示"),
    )

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        title = str(context.site_settings.get("page_title", "网络核查演示"))
        context.page.set_content(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<title>{escape(title)}</title>"
            "<style>body{font-family:'Microsoft YaHei',sans-serif;margin:40px;}"
            "h1{font-size:24px;}p{font-size:16px;}</style></head><body>"
            f"<h1>{escape(title)}</h1><p>核查主体：{escape(subject)}</p>"
            "<p>本页仅用于验证 kwtool 框架，不代表任何网站的核查结果。</p>"
            "</body></html>"
        )
        context.report_status(f"已打开演示页面：{subject}")
        if context.site_settings.get("manual_step", True):
            context.wait_for_user(f"请查看右侧页面，完成后点击“继续”：{subject}")
        saved = context.save_pdf()
        context.report_status(f"已保存 PDF：{saved}")
