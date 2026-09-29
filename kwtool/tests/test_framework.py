from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event

import pytest

from kwtool.adapters.base import RunCancelled, RunContext
from kwtool.adapters.demo import DemoAdapter
from kwtool.models import PdfSettings
from kwtool.output import OutputPlan, safe_filename, save_download, save_pdf_bytes, validate_template


class FakePage:
    def __init__(self) -> None:
        self.html = ""
        self.pdf_options: dict = {}

    def set_content(self, html: str) -> None:
        self.html = html

    def pdf(self, **options) -> bytes:
        self.pdf_options = options
        return b"%PDF-1.4\nlocal demo\n"


class FakeDownload:
    suggested_filename = "报告.pdf"

    def save_as(self, destination: Path) -> None:
        destination.write_bytes(b"report")


def test_template_renders_chinese_subject_and_windows_safe_names(tmp_path: Path) -> None:
    plan = OutputPlan(tmp_path, "Output/[date]/[id]-[object]-[website].pdf", datetime(2026, 9, 29))
    path = plan.render(2, "张三/某公司", "工商*信息")
    assert path == tmp_path / "Output" / "260929" / "002-张三_某公司-工商_信息.pdf"
    assert safe_filename("CON") == "_CON"


@pytest.mark.parametrize("template", ["", "Output/[unknown].pdf", "Output/[object].txt", "../out.pdf", "Output/[object.pdf"])
def test_invalid_templates_fail_before_batch(template: str) -> None:
    with pytest.raises(ValueError):
        validate_template(template)


def test_existing_pdf_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "report.pdf"
    target.write_bytes(b"old")
    saved = save_pdf_bytes(b"new", target)
    assert target.read_bytes() == b"old"
    assert saved.name == "report_2.pdf"
    assert saved.read_bytes() == b"new"


def test_attachments_keep_extension_and_avoid_collisions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "001-主体-网站.pdf"
    first = save_download(FakeDownload(), pdf_path)  # type: ignore[arg-type]
    second = save_download(FakeDownload(), pdf_path)  # type: ignore[arg-type]
    assert first.name == "报告.pdf"
    assert second.name == "报告_2.pdf"
    assert first.parent.name == "001-主体-网站_附件"


def test_demo_adapter_waits_and_prints_with_selected_options(tmp_path: Path) -> None:
    page = FakePage()
    messages: list[str] = []
    prompts: list[str] = []
    artifacts: list[Path] = []
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "demo.pdf",
        pdf_settings=PdfSettings(landscape=True, scale=0.8, margin_mm=5, print_background=False),
        site_settings={"manual_step": True, "page_title": "检查结果"},
        stop_event=Event(),
        report_status=messages.append,
        request_user_continue=prompts.append,
        artifacts=artifacts,
    )
    DemoAdapter().run_one(context, "主体 <A>")
    assert "主体 &lt;A&gt;" in page.html
    assert len(prompts) == 1
    assert page.pdf_options["landscape"] is True
    assert page.pdf_options["scale"] == 0.8
    assert artifacts == [tmp_path / "demo.pdf"]
    assert artifacts[0].read_bytes().startswith(b"%PDF")


def test_stop_interrupts_manual_step(tmp_path: Path) -> None:
    stop = Event()
    stop.set()
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=FakePage(),  # type: ignore[arg-type]
        pdf_path=tmp_path / "stopped.pdf",
        pdf_settings=PdfSettings(),
        site_settings={},
        stop_event=stop,
        report_status=lambda _: None,
        request_user_continue=lambda _: None,
        artifacts=[],
    )
    with pytest.raises(RunCancelled):
        DemoAdapter().run_one(context, "主体")
    assert not (tmp_path / "stopped.pdf").exists()
