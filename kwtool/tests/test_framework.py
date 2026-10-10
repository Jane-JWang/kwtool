from __future__ import annotations

from datetime import datetime
import io
from pathlib import Path
from threading import Event
import re

import pytest
from pypdf import PdfWriter

from kwtool.adapters.base import RunCancelled, RunContext, SiteUnavailable, print_header
from kwtool.adapters.demo import DemoAdapter
from kwtool.adapters import registered_adapters
from kwtool.adapters.gsxt import GsxtAdapter
from kwtool.models import BatchRequest, BrowserLayout, PdfSettings
from kwtool.mail163 import MAIL_TITLE, SENDER, SentReport, _message_date, _not_before_click, _pdf_not_before_click, _refresh_seconds
from kwtool.output import OutputPlan, safe_filename, save_download, save_pdf_bytes, validate_template
from kwtool.worker import BrowserWorker
from kwtool.ui import format_batch_summary
from kwtool.preferences import profile_path


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


def test_demo_adapter_prints_with_browser_headers(tmp_path: Path) -> None:
    page = FakePage()
    messages: list[str] = []
    artifacts: list[Path] = []
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "demo.pdf",
        pdf_settings=PdfSettings(landscape=True, scale=0.8, margin_mm=5, print_background=False),
        site_settings={"manual_step": True, "page_title": "检查结果"},
        stop_event=Event(),
        report_status=messages.append,
        artifacts=artifacts,
    )
    DemoAdapter().run_one(context, "主体 <A>")
    assert "主体 &lt;A&gt;" in page.html
    assert page.pdf_options["display_header_footer"] is True
    assert page.pdf_options["format"] == "A4"
    assert page.pdf_options["landscape"] is True
    assert page.pdf_options["scale"] == 0.8
    assert page.pdf_options["print_background"] is False
    assert page.pdf_options["margin"]["left"] == "5mm"
    assert re.search(r"\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2}", page.pdf_options["header_template"])
    assert 'class="title"' in page.pdf_options["header_template"]
    assert 'class="url"' in page.pdf_options["footer_template"]
    assert 'class="pageNumber"' in page.pdf_options["footer_template"]
    assert 'position:absolute' in page.pdf_options["footer_template"]
    assert artifacts == [tmp_path / "demo.pdf"]
    assert artifacts[0].read_bytes().startswith(b"%PDF")


def test_print_header_formats_local_datetime_without_zero_padded_date() -> None:
    assert "2026/9/29 18:33" in print_header(datetime(2026, 9, 29, 18, 33))


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
        artifacts=[],
    )
    with pytest.raises(RunCancelled):
        DemoAdapter().run_one(context, "主体")
    assert not (tmp_path / "stopped.pdf").exists()


def test_user_action_indicator_resets_after_wait(tmp_path: Path) -> None:
    states: list[bool] = []
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=FakePage(),  # type: ignore[arg-type]
        pdf_path=tmp_path / "unused.pdf",
        pdf_settings=PdfSettings(),
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
        report_user_action=states.append,
    )
    context.wait_until(lambda: True, "等待用户", user_action=True)
    assert states == [True, False]
    with pytest.raises(TimeoutError):
        context.wait_until(lambda: False, "等待超时", timeout_seconds=0, user_action=True)
    assert states == [True, False, True, False]


def test_wait_until_pumps_browser_events(tmp_path: Path) -> None:
    class EventPage:
        dispatched = False

        def wait_for_timeout(self, milliseconds: int) -> None:
            assert milliseconds == 250
            self.dispatched = True

    page = EventPage()
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "unused.pdf",
        pdf_settings=PdfSettings(),
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
    )
    context.wait_until(lambda: page.dispatched, "等待新标签页", timeout_seconds=1)
    assert page.dispatched


def test_closed_chrome_is_relaunched_before_next_batch(tmp_path: Path, monkeypatch) -> None:
    worker = BrowserWorker(tmp_path / "profile", tmp_path, BrowserLayout(0, 0, 800, 600))
    class BrowserConnection:
        connected = False

        def is_connected(self) -> bool:
            return self.connected

        def close(self) -> None:
            pass

    class Context:
        pages = []

    old_context = Context()
    new_context = Context()
    connection = BrowserConnection()
    worker._connected_browser = connection  # type: ignore[assignment]
    launched: list[bool] = []
    monkeypatch.setattr(worker, "_launch", lambda _: launched.append(True) or new_context)
    assert worker._ensure_browser(object(), old_context) is new_context  # type: ignore[arg-type]
    assert launched == [True]
    connection.connected = True
    worker._connected_browser = connection  # type: ignore[assignment]
    assert worker._ensure_browser(object(), old_context) is new_context  # type: ignore[arg-type]
    assert launched == [True, True]


def test_gsxt_is_registered_with_site_only_pdf_defaults() -> None:
    adapters = registered_adapters()
    assert isinstance(adapters["gsxt"], GsxtAdapter)
    assert adapters["gsxt"].default_pdf_settings.scale == 0.35
    assert adapters["gsxt"].default_pdf_settings.print_background is True
    assert next(spec.default for spec in adapters["gsxt"].settings_schema if spec.key == "download_163_report") is False
    refresh = next(spec for spec in adapters["gsxt"].settings_schema if spec.key == "mail_refresh_seconds")
    assert (refresh.default, refresh.minimum, refresh.maximum) == (15, 5, 300)
    assert adapters["demo"].default_pdf_settings.scale == 1.0


def test_163_mail_date_parser_rejects_ambiguous_dates() -> None:
    now = datetime(2026, 10, 10, 15, 0)
    assert SENDER == "gsxt@samr.gov.cn"
    assert MAIL_TITLE == "国家企业信用信息公示系统-企业报告"
    assert _message_date("2026-10-10 14:30", now) == datetime(2026, 10, 10, 14, 30)
    assert _message_date("2026年10月10日 13:56 (星期六)", now) == datetime(2026, 10, 10, 13, 56)
    assert _message_date("10/10 14:30", now) == datetime(2026, 10, 10, 14, 30)
    assert _message_date("今天 14:30", now) == datetime(2026, 10, 10, 14, 30)
    assert _message_date("14:30", now) is None


def test_163_mail_must_be_newer_than_report_click() -> None:
    clicked = datetime(2026, 10, 10, 14, 2, 22)
    assert not _not_before_click(datetime(2026, 10, 10, 14, 1), clicked)
    assert _not_before_click(datetime(2026, 10, 10, 14, 2), clicked)
    assert _not_before_click(datetime(2026, 10, 10, 14, 3), clicked)
    assert _not_before_click(datetime(2026, 10, 10, 14, 2), datetime(2026, 10, 10, 14, 2))


def test_163_report_pdf_creation_time_resolves_same_minute() -> None:
    clicked = datetime(2026, 10, 10, 16, 39, 1)
    received = datetime(2026, 10, 10, 16, 39)

    def pdf_at(creation: str | None) -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        if creation:
            writer.add_metadata({"/CreationDate": creation})
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()

    assert _pdf_not_before_click(pdf_at("D:20261010163920+08'00'"), clicked, received)
    assert not _pdf_not_before_click(pdf_at("D:20261010163859+08'00'"), clicked, received)
    assert not _pdf_not_before_click(pdf_at(None), clicked, received)


def test_163_refresh_interval_is_bounded() -> None:
    assert [_refresh_seconds(value) for value in (None, 2, 15, 45, 900)] == [15, 5, 15, 45, 300]


def test_gsxt_batch_collects_only_confirmed_reports(tmp_path: Path, monkeypatch) -> None:
    class Adapter(GsxtAdapter):
        def run_one(self, context, subject):
            context.save_pdf()
            if subject == "已发送企业":
                context.report_sent_at = datetime(2026, 10, 10, 14, 30)

    class Browser:
        pages = [FakePage()]

    received: list[SentReport] = []
    intervals: list[int] = []
    def collect(browser, reports, stop, progress, user_action, *, refresh_seconds):
        received.extend(reports)
        intervals.append(refresh_seconds)
        return [reports[0].pdf_path.with_name("报告.pdf")], []

    monkeypatch.setattr("kwtool.worker.registered_adapters", lambda: {"gsxt": Adapter()})
    monkeypatch.setattr("kwtool.worker.collect_reports", collect)
    worker = BrowserWorker(tmp_path / "profile", tmp_path, BrowserLayout(0, 0, 800, 600))
    results: list[tuple] = []
    worker.mail_result.connect(lambda *args: results.append(args))
    request = BatchRequest("gsxt", ("已发送企业", "未发送企业"), "[id]-[object].pdf", PdfSettings(),
                           site_settings={"download_163_report": True, "mail_refresh_seconds": 45})
    worker._run_batch(Browser(), request)  # type: ignore[arg-type]
    assert [report.subject for report in received] == ["已发送企业"]
    assert results == [(1, 0, "")]
    assert intervals == [45]


def test_gsxt_keeps_saved_pdf_when_mail_confirmation_fails(tmp_path: Path, monkeypatch) -> None:
    adapter = GsxtAdapter()
    page = FakePage()
    events: list[str] = []
    monkeypatch.setattr(adapter, "_find_company", lambda context, subject: page)
    monkeypatch.setattr(adapter, "_load_columns", lambda context, page: events.append("columns"))

    def failed_send(context, page):
        events.append("mail")
        raise TimeoutError("邮件未确认")

    monkeypatch.setattr(adapter, "_send_report", failed_send)
    artifacts: list[Path] = []
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "report.pdf",
        pdf_settings=adapter.default_pdf_settings,
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=artifacts,
    )
    with pytest.raises(TimeoutError, match="邮件未确认"):
        adapter.run_one(context, "某公司")
    assert events == ["columns", "mail"]
    assert artifacts == [tmp_path / "report_网站.pdf"]
    assert page.pdf_options["scale"] == 0.35
    assert page.pdf_options["margin"]["left"] == "6.35mm"


def test_batch_continues_after_mail_failure_and_reports_saved_pdf(tmp_path: Path, monkeypatch) -> None:
    class PartialAdapter(GsxtAdapter):
        def run_one(self, context, subject):
            context.save_pdf()
            if subject == "失败企业":
                raise TimeoutError("邮件未确认")

    class Browser:
        pages = [FakePage()]

    monkeypatch.setattr("kwtool.worker.registered_adapters", lambda: {"gsxt": PartialAdapter()})
    worker = BrowserWorker(tmp_path / "profile", tmp_path, BrowserLayout(0, 0, 800, 600))
    rows: list[tuple] = []
    worker.row_result.connect(lambda *args: rows.append(args))
    request = BatchRequest("gsxt", ("失败企业", "成功企业"), "[id]-[object].pdf", PdfSettings())
    assert worker._run_batch(Browser(), request) is False  # type: ignore[arg-type]
    assert [row[2] for row in rows] == [False, True]
    assert "邮件未确认" in rows[0][3]
    assert "已保存" in rows[0][3]
    assert (tmp_path / "001-失败企业.pdf").exists()
    assert (tmp_path / "002-成功企业.pdf").exists()


def test_site_outage_stops_batch_before_repeating_requests(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class OfflineAdapter(GsxtAdapter):
        def run_one(self, context, subject):
            calls.append(subject)
            raise SiteUnavailable("企信网返回 HTTP 521")

    class Browser:
        pages = [FakePage()]

    monkeypatch.setattr("kwtool.worker.registered_adapters", lambda: {"gsxt": OfflineAdapter()})
    worker = BrowserWorker(tmp_path / "profile", tmp_path, BrowserLayout(0, 0, 800, 600))
    batches: list[tuple] = []
    worker.batch_result.connect(lambda *args: batches.append(args))
    request = BatchRequest("gsxt", ("企业甲", "企业乙"), "[id]-[object].pdf", PdfSettings())
    assert worker._run_batch(Browser(), request) is False  # type: ignore[arg-type]
    assert calls == ["企业甲"]
    assert batches == [(0, 1, True)]


def test_gsxt_waits_before_reporting_persistent_error(tmp_path: Path, monkeypatch) -> None:
    class ErrorResponse:
        status = 521

    class ErrorPage:
        def __init__(self):
            self.visited: list[str] = []
            self.url = "about:blank"

        def goto(self, url, **kwargs):
            self.visited.append(url)
            self.url = url
            return ErrorResponse()

    page = ErrorPage()
    monkeypatch.setattr("kwtool.adapters.gsxt._visible", lambda *_: False)
    monkeypatch.setattr("kwtool.adapters.gsxt._login_required", lambda *_: False)
    clock = iter([0, 181])
    monkeypatch.setattr("kwtool.adapters.gsxt.time.monotonic", lambda: next(clock))
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "unused.pdf",
        pdf_settings=PdfSettings(),
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
    )
    with pytest.raises(SiteUnavailable, match="HTTP 521"):
        GsxtAdapter()._search_page(context)
    assert page.visited == ["https://www.gsxt.gov.cn/"]


def test_batch_summary_keeps_failure_reason_visible() -> None:
    summary = format_batch_summary(0, 1, True, 5, "001 企业甲：企信网返回 HTTP 521")
    assert "失败 1" in summary
    assert "未处理 4" in summary
    assert "企信网返回 HTTP 521" in summary


def test_profile_override_keeps_existing_profile_untouched(tmp_path: Path, monkeypatch) -> None:
    alternate = tmp_path / "fresh chrome"
    monkeypatch.setenv("KWTOOL_PROFILE_DIR", str(alternate))
    assert profile_path() == alternate
    assert not alternate.exists()
