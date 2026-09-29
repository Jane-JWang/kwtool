from __future__ import annotations

import queue
import logging
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal
from playwright.sync_api import BrowserContext, Error as PlaywrightError, sync_playwright

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunCancelled, RunContext
from kwtool.models import BatchRequest, BrowserLayout
from kwtool.output import OutputPlan, available_path


class BrowserWorker(QThread):
    browser_status = Signal(bool, str)
    batch_state = Signal(str)
    progress = Signal(str)
    row_result = Signal(int, str, bool, str)
    batch_result = Signal(int, int, bool)

    def __init__(self, profile_dir: Path, output_base: Path, layout: BrowserLayout) -> None:
        super().__init__()
        self._profile_dir = profile_dir
        self._output_base = output_base
        self._layout = layout
        self._commands: queue.Queue[BatchRequest | None] = queue.Queue()
        self._stop = Event()
        self._shutdown = Event()

    def submit(self, request: BatchRequest) -> None:
        self._stop.clear()
        self._commands.put(request)

    def stop_batch(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        self._shutdown.set()
        self.stop_batch()
        self._commands.put(None)

    def _launch(self, playwright) -> BrowserContext:
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        layout = self._layout
        context = playwright.chromium.launch_persistent_context(
            self._profile_dir,
            channel="chrome",
            headless=False,
            accept_downloads=True,
            no_viewport=True,
            args=[
                f"--window-position={layout.x},{layout.y}",
                f"--window-size={layout.width},{layout.height}",
            ],
        )
        context.set_default_timeout(15000)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            session = context.new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            session.send("Browser.setWindowBounds", {
                "windowId": window["windowId"],
                "bounds": {
                    "left": layout.x,
                    "top": layout.y,
                    "width": layout.width,
                    "height": layout.height,
                    "windowState": "normal",
                },
            })
            session.detach()
        except PlaywrightError:
            pass  # Chrome's launch flags already requested the layout.
        self.browser_status.emit(True, "Chrome 已就绪")
        return context

    def _run_batch(self, browser: BrowserContext, request: BatchRequest) -> bool:
        adapter = registered_adapters()[request.site_key]
        output = OutputPlan(self._output_base, request.output_template, datetime.now())
        successes = 0
        failures = 0
        stopped = False
        browser_closed = False
        self.batch_state.emit("running")
        for index, subject in enumerate(request.subjects, 1):
            if self._stop.is_set() or self._shutdown.is_set():
                stopped = True
                break
            self.progress.emit(f"正在处理 {index}/{len(request.subjects)}：{subject}")
            artifacts: list[Path] = []
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                run = RunContext(
                    browser=browser,
                    page=page,
                    pdf_path=available_path(output.render(index, subject, adapter.display_name)),
                    pdf_settings=request.pdf,
                    site_settings=request.site_settings,
                    stop_event=self._stop,
                    report_status=self.progress.emit,
                    artifacts=artifacts,
                )
                adapter.run_one(run, subject)
                run.check_stop()
                successes += 1
                self.row_result.emit(index, subject, True, ", ".join(str(p) for p in artifacts))
            except RunCancelled:
                stopped = True
                break
            except Exception as exc:
                logging.getLogger("kwtool").exception("主体处理失败：%s", subject)
                failures += 1
                self.row_result.emit(index, subject, False, str(exc))
                if isinstance(exc, PlaywrightError) and "closed" in str(exc).lower():
                    self.browser_status.emit(False, "Chrome 已关闭，下次查询将尝试重新启动")
                    browser_closed = True
                    stopped = True
                    break
        self.batch_state.emit("idle")
        self.batch_result.emit(successes, failures, stopped)
        return browser_closed

    def run(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser: BrowserContext | None = None
                try:
                    browser = self._launch(playwright)
                except Exception as exc:
                    self.browser_status.emit(False, f"无法启动 Chrome：{exc}")
                while not self._shutdown.is_set():
                    try:
                        request = self._commands.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if request is None:
                        continue
                    if browser is None:
                        try:
                            browser = self._launch(playwright)
                        except Exception as exc:
                            self.browser_status.emit(False, f"无法启动 Chrome：{exc}")
                            self.batch_result.emit(0, 0, True)
                            continue
                    try:
                        browser_closed = self._run_batch(browser, request)
                        if browser_closed:
                            browser = None
                    except Exception as exc:
                        logging.getLogger("kwtool").exception("批次执行失败")
                        self.progress.emit(f"批次执行失败：{exc}")
                        self.batch_state.emit("idle")
                        self.batch_result.emit(0, 0, True)
                    if browser is not None:
                        try:
                            if not browser.pages:
                                browser = None
                        except PlaywrightError:
                            browser = None
                if browser is not None:
                    try:
                        browser.close()
                    except PlaywrightError:
                        pass
        except Exception as exc:
            logging.getLogger("kwtool").exception("Playwright 初始化失败")
            self.browser_status.emit(False, f"Playwright 初始化失败：{exc}")
            self.batch_state.emit("idle")
            if not self._commands.empty():
                self.batch_result.emit(0, 0, True)
