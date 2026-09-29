from __future__ import annotations

import queue
import logging
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal
from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, sync_playwright

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunCancelled, RunContext, SiteUnavailable
from kwtool.models import BatchRequest, BrowserLayout
from kwtool.output import OutputPlan, available_path


class BrowserWorker(QThread):
    browser_status = Signal(bool, str)
    batch_state = Signal(str)
    progress = Signal(str)
    user_action = Signal(bool)
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
        self._connected_browser: Browser | None = None
        self._chrome_process: subprocess.Popen | None = None

    @staticmethod
    def _chrome_path() -> Path:
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if candidate.is_file():
                    return candidate
        raise FileNotFoundError("未找到 Google Chrome，请先安装 Chrome")

    def _start_chrome(self) -> str:
        if self._chrome_process is not None and self._chrome_process.poll() is None:
            self._chrome_process.terminate()
            self._chrome_process.wait(timeout=5)
        profile = self._profile_dir / "cdp"
        profile.mkdir(parents=True, exist_ok=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        layout = self._layout
        self._chrome_process = subprocess.Popen([
            str(self._chrome_path()),
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--window-position={layout.x},{layout.y}",
            f"--window-size={layout.width},{layout.height}",
            "about:blank",
        ])
        return f"http://127.0.0.1:{port}"

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
        layout = self._layout
        endpoint = os.environ.get("KWTOOL_CDP_URL", "").strip()
        if endpoint:
            try:
                parsed = urlparse(endpoint)
                valid = (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                         and parsed.port is not None and not parsed.username and not parsed.password
                         and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("KWTOOL_CDP_URL 必须是本机 HTTP 调试地址，例如 http://127.0.0.1:9229")
        else:
            endpoint = self._start_chrome()
        deadline = time.monotonic() + 15
        while True:
            try:
                self._connected_browser = playwright.chromium.connect_over_cdp(endpoint, timeout=1500)
                break
            except PlaywrightError:
                if time.monotonic() >= deadline or (self._chrome_process is not None and self._chrome_process.poll() is not None):
                    raise
                time.sleep(0.25)
        if not self._connected_browser.contexts:
            self._connected_browser.close()
            self._connected_browser = None
            raise RuntimeError("已连接 Chrome，但未找到浏览器资料")
        context = self._connected_browser.contexts[0]
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
        self.browser_status.emit(True, "已连接 Chrome")
        return context

    def _run_batch(self, browser: BrowserContext, request: BatchRequest) -> bool:
        adapter = registered_adapters()[request.site_key]
        output = OutputPlan(self._output_base, request.output_template, datetime.now())
        successes = 0
        failures = 0
        stopped = False
        browser_closed = False
        self.batch_state.emit("running")
        self.user_action.emit(False)
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
                    report_user_action=self.user_action.emit,
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
                detail = str(exc)
                if artifacts:
                    detail += "；已保存：" + ", ".join(str(path) for path in artifacts)
                self.row_result.emit(index, subject, False, detail)
                if isinstance(exc, SiteUnavailable):
                    stopped = True
                    break
                if isinstance(exc, PlaywrightError) and "closed" in str(exc).lower():
                    self.browser_status.emit(False, "Chrome 已关闭，下次查询将尝试重新启动")
                    browser_closed = True
                    stopped = True
                    break
        self.batch_state.emit("idle")
        self.user_action.emit(False)
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
                        if browser is not None:
                            try:
                                if not browser.pages:
                                    browser.new_page()
                                    self.browser_status.emit(True, "Chrome 页面已恢复")
                            except PlaywrightError:
                                browser = None
                        if browser is None and self._chrome_process is not None and self._chrome_process.poll() is not None:
                            try:
                                browser = self._launch(playwright)
                            except Exception as exc:
                                self.browser_status.emit(False, f"无法重新启动 Chrome：{exc}")
                                time.sleep(2)
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
                                browser.new_page()
                        except PlaywrightError:
                            browser = None
                if self._connected_browser is not None:
                    try:
                        self._connected_browser.close()
                    except PlaywrightError:
                        pass
                    self._connected_browser = None
                if self._chrome_process is not None and self._chrome_process.poll() is None:
                    self._chrome_process.terminate()
        except Exception as exc:
            logging.getLogger("kwtool").exception("Playwright 初始化失败")
            self.browser_status.emit(False, f"Playwright 初始化失败：{exc}")
            self.batch_state.emit("idle")
            if not self._commands.empty():
                self.batch_result.emit(0, 0, True)
