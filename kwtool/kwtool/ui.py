from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from kwtool.adapters import registered_adapters
from kwtool.models import BatchRequest, BrowserLayout, FieldSpec, PdfSettings
from kwtool.output import OutputPlan, validate_template
from kwtool.preferences import default_template, load_settings, log_directory, migrate_legacy_logs, profile_path, save_settings
from kwtool import __version__
from kwtool.updater import DownloadWorker, ReleaseWorker, launch_swap, version_key
from kwtool.worker import BrowserWorker


def documents_base() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents) / "kwtool" if documents else Path.home() / "Documents" / "kwtool"


def format_batch_summary(successes: int, failures: int, stopped: bool, total: int, last_error: str) -> str:
    summary = f"批次{'已停止' if stopped else '完成'}：成功 {successes}，失败 {failures}"
    if stopped and total > successes + failures:
        summary += f"，未处理 {total - successes - failures}"
    if last_error:
        summary += f"\n最近失败：{last_error}"
    return summary


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("kwtool · 网络核查")
        self._adapters = registered_adapters()
        self._saved = load_settings()
        saved_sites = self._saved.get("site_settings", {})
        self._site_values: dict[str, dict] = dict(saved_sites) if isinstance(saved_sites, dict) else {}
        saved_pdf = self._saved.get("site_pdf_settings", {})
        self._site_pdf_values: dict[str, dict] = dict(saved_pdf) if isinstance(saved_pdf, dict) else {}
        self._current_site_key: str | None = None
        self._site_widgets: dict[str, QWidget] = {}
        self._closing = False
        self._close_connected = False
        self._running = False
        self._last_row_error = ""
        self._browser_error = ""
        self._mail_summary = ""
        self._output_base = documents_base()
        migrate_legacy_logs()
        log_path = log_directory() / "kwtool.log"
        self._logger = logging.getLogger("kwtool")
        self._logger.setLevel(logging.INFO)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "无法写入日志", f"请检查用户数据目录权限：\n{log_path}\n{exc}")
            raise
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.addHandler(handler)
        self._total = 0
        self._layout_windows()
        self._build_ui()
        self._restore_settings()
        self._releases: list[dict] = []

        self._create_worker()
        self._worker.start()

    def _create_worker(self) -> None:
        self._worker = BrowserWorker(profile_path(), self._output_base, self._browser_layout)
        self._worker.browser_status.connect(self._on_browser_status)
        self._worker.batch_state.connect(self._on_batch_state)
        self._worker.progress.connect(self._log)
        self._worker.user_action.connect(self._set_user_action)
        self._worker.row_result.connect(self._on_row_result)
        self._worker.batch_result.connect(self._on_batch_result)
        self._worker.mail_result.connect(self._on_mail_result)

    def _layout_windows(self) -> None:
        screen = QApplication.primaryScreen()
        assert screen is not None
        area = screen.availableGeometry()
        left_width = max(340, round(area.width() * 0.2))
        left_width = min(left_width, round(area.width() * 0.4))
        self.setGeometry(area.x(), area.y(), left_width, area.height())
        self._browser_layout = BrowserLayout(
            area.x() + left_width,
            area.y(),
            area.width() - left_width,
            area.height(),
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        system = QGroupBox("系统设置")
        system_layout = QVBoxLayout(system)
        system_layout.setContentsMargins(8, 8, 8, 8)
        update_row = QHBoxLayout()
        self.update_button = QPushButton("检查更新")
        self.update_button.clicked.connect(self._check_updates)
        update_row.addWidget(self.update_button)
        self.developer_mode_check = QCheckBox("开发者模式")
        self.developer_mode_check.setToolTip("保存页面截图和结构到用户数据目录下的 .log 文件夹")
        update_row.addWidget(self.developer_mode_check)
        system_layout.addLayout(update_row)
        rollback_row = QHBoxLayout()
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(100)
        self.version_combo.setPlaceholderText("选择历史版本")
        self.rollback_button = QPushButton("回滚")
        self.rollback_button.setEnabled(False)
        self.rollback_button.clicked.connect(self._rollback)
        rollback_row.addWidget(self.version_combo, 1)
        rollback_row.addWidget(self.rollback_button)
        system_layout.addLayout(rollback_row)
        root.addWidget(system)
        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter)

        upper = QGroupBox("查询")
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(12, 12, 12, 12)
        upper_layout.addWidget(QLabel("网站"))
        self.site_combo = QComboBox()
        for adapter in self._adapters.values():
            self.site_combo.addItem(adapter.display_name, adapter.key)
        upper_layout.addWidget(self.site_combo)

        upper_layout.addWidget(QLabel("待查询主体（每行一个）"))
        self.subjects_edit = QPlainTextEdit()
        self.subjects_edit.setPlaceholderText("每行一个主体，例如：张三(身份证:123456)")
        self.subjects_edit.setFixedHeight(110)
        upper_layout.addWidget(self.subjects_edit)

        template_header = QHBoxLayout()
        template_header.addWidget(QLabel("输出路径模板"))
        help_button = QToolButton()
        help_button.setText("ⓘ")
        help_button.setAccessibleName("输出路径模板说明")
        help_text = "[id] - 批次序号\n[object] - 查询主体\n[website] - 网站名称\n[date] - 日期 YYMMDD\n[time] - 时间 HHMMSS\n[year] - 年\n[month] - 月\n[day] - 日"
        help_button.setToolTip(help_text)
        help_button.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
        help_button.clicked.connect(
            lambda: QToolTip.showText(help_button.mapToGlobal(help_button.rect().bottomLeft()), help_text, help_button)
        )
        template_header.addWidget(help_button)
        open_output_button = QToolButton()
        open_output_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_output_button.setToolTip("打开输出目录")
        open_output_button.setAccessibleName("打开输出目录")
        open_output_button.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
        open_output_button.clicked.connect(self._open_output_directory)
        template_header.addWidget(open_output_button)
        template_header.addStretch()
        upper_layout.addLayout(template_header)
        self.template_edit = QLineEdit()
        upper_layout.addWidget(self.template_edit)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("查询")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        upper_layout.addLayout(buttons)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v of %m")
        upper_layout.addWidget(self.progress_bar)
        self.progress_bar.hide()

        status_row = QHBoxLayout()
        self.status_light = QLabel()
        self.status_light.setFixedSize(12, 12)
        self.status_light.setAccessibleName("状态指示灯")
        status_row.addWidget(self.status_light, alignment=Qt.AlignmentFlag.AlignTop)
        self._set_user_action(False)
        self.status_label = QLabel("正在启动 Chrome…")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        upper_layout.addLayout(status_row)
        upper_layout.addStretch(1)

        lower = QGroupBox("网页专属配置")
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        lower_layout.addWidget(scroll)
        settings_panel = QWidget()
        self.site_form = QFormLayout(settings_panel)
        self.site_form.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(settings_panel)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["纵向", "横向"])
        self.site_form.addRow("方向", self.orientation_combo)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(10, 200)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setValue(100)
        self.site_form.addRow("缩放", self.scale_spin)
        self.background_check = QCheckBox("打印背景")
        self.site_form.addRow(self.background_check)

        splitter.addWidget(upper)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([self.height() * 2 // 3, self.height() // 3])

        self.run_button.clicked.connect(self._start_batch)
        self.stop_button.clicked.connect(self._stop_batch)
        self.site_combo.currentIndexChanged.connect(self._render_site_fields)

    def _restore_settings(self) -> None:
        self.subjects_edit.setPlainText(str(self._saved.get("subjects", "")))
        self.template_edit.setText(default_template(self._saved))
        self.developer_mode_check.setChecked(bool(self._saved.get("developer_mode", self._saved.get("diagnostics", False))))
        saved_key = self._saved.get("site_key")
        index = self.site_combo.findData(saved_key)
        if index >= 0:
            self.site_combo.setCurrentIndex(index)
        self._render_site_fields()

    def _read_field(self, spec: FieldSpec, widget: QWidget) -> bool | str | int | float:
        if spec.kind == "bool":
            return widget.isChecked()  # type: ignore[attr-defined]
        if spec.kind in {"integer", "decimal"}:
            return widget.value()  # type: ignore[attr-defined]
        if spec.kind == "choice":
            return widget.currentText()  # type: ignore[attr-defined]
        return widget.text()  # type: ignore[attr-defined]

    def _capture_site_fields(self) -> None:
        if self._current_site_key is None:
            return
        self._site_pdf_values[self._current_site_key] = {
            "landscape": self.orientation_combo.currentIndex() == 1,
            "scale_percent": self.scale_spin.value(),
            "print_background": self.background_check.isChecked(),
        }
        adapter = self._adapters[self._current_site_key]
        self._site_values[self._current_site_key] = {
            spec.key: self._read_field(spec, self._site_widgets[spec.key])
            for spec in adapter.settings_schema
        }

    def _render_site_fields(self) -> None:
        self._capture_site_fields()
        while self.site_form.rowCount() > 3:
            self.site_form.removeRow(3)
        self._site_widgets = {}
        key = self.site_combo.currentData()
        if key not in self._adapters:
            self._current_site_key = None
            return
        adapter = self._adapters[key]
        if adapter.required_kwargs:
            self.site_form.addRow(QLabel("必填字段:" + "、".join(adapter.required_kwargs)))
        if adapter.optional_kwargs:
            self.site_form.addRow(QLabel("可选字段:" + "、".join(adapter.optional_kwargs)))
        saved = self._site_values.get(key, {})
        defaults = adapter.default_pdf_settings
        pdf_saved = self._site_pdf_values.get(key, {})
        if not isinstance(pdf_saved, dict):
            pdf_saved = {}
        self.orientation_combo.setCurrentIndex(1 if pdf_saved.get("landscape", defaults.landscape) else 0)
        self.scale_spin.setValue(int(pdf_saved.get("scale_percent", round(defaults.scale * 100))))
        self.background_check.setChecked(bool(pdf_saved.get("print_background", defaults.print_background)))
        if not isinstance(saved, dict):
            saved = {}
        for spec in adapter.settings_schema:
            value = saved.get(spec.key, spec.default)
            if spec.kind == "bool":
                widget = QCheckBox(spec.label)
                widget.setChecked(bool(value))
                self.site_form.addRow(widget)
            elif spec.kind == "integer":
                widget = QSpinBox()
                if (spec.minimum, spec.maximum) == (0.0, 10.0):
                    widget.setRange(-1_000_000, 1_000_000)
                else:
                    widget.setRange(int(spec.minimum), int(spec.maximum))
                try:
                    widget.setValue(int(value))
                except (TypeError, ValueError):
                    widget.setValue(int(spec.default))
                self.site_form.addRow(spec.label, widget)
            elif spec.kind == "decimal":
                widget = QDoubleSpinBox()
                widget.setDecimals(2)
                widget.setRange(spec.minimum, spec.maximum)
                widget.setSingleStep(spec.step)
                try:
                    widget.setValue(float(value))
                except (TypeError, ValueError):
                    widget.setValue(float(spec.default))
                self.site_form.addRow(spec.label, widget)
            elif spec.kind == "choice":
                widget = QComboBox()
                widget.addItems(spec.choices)
                selected = widget.findText(str(value))
                if selected >= 0:
                    widget.setCurrentIndex(selected)
                self.site_form.addRow(spec.label, widget)
            else:
                widget = QLineEdit(str(value))
                self.site_form.addRow(spec.label, widget)
            self._site_widgets[spec.key] = widget
        self._current_site_key = key

    def _pdf_settings(self) -> PdfSettings:
        return PdfSettings(
            landscape=self.orientation_combo.currentIndex() == 1,
            scale=self.scale_spin.value() / 100,
            print_background=self.background_check.isChecked(),
        ).validated()

    def _save_preferences(self) -> None:
        self._capture_site_fields()
        save_settings({
            "site_key": self.site_combo.currentData(),
            "subjects": self.subjects_edit.toPlainText(),
            "output_template": self.template_edit.text(),
            "site_settings": self._site_values,
            "site_pdf_settings": self._site_pdf_values,
            "developer_mode": self.developer_mode_check.isChecked(),
        })

    def _check_updates(self) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("检查中…")
        self._release_worker = ReleaseWorker()
        self._release_worker.found.connect(self._show_releases)
        self._release_worker.failed.connect(self._update_failed)
        self._release_worker.start()

    def _show_releases(self, releases: list[dict]) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("检查更新")
        self._releases = releases
        self.version_combo.clear()
        current = version_key(__version__)
        for release in releases:
            if current is not None and version_key(release["tag"]) < current:
                self.version_combo.addItem(release["tag"], release)
        self.rollback_button.setEnabled(self.version_combo.count() > 0 and getattr(sys, "frozen", False))
        latest = releases[0] if releases else None
        if latest and current is not None and version_key(latest["tag"]) > current:
            if QMessageBox.question(self, "发现新版本", f"当前版本：{__version__}\n新版本：{latest['tag']}\n现在下载安装吗？") == QMessageBox.StandardButton.Yes:
                self._install_release(latest)
        else:
            QMessageBox.information(self, "检查更新", f"当前版本 {__version__}，没有发现更新。")

    def _update_failed(self, message: str) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("检查更新")
        QMessageBox.warning(self, "更新失败", message)

    def _rollback(self) -> None:
        release = self.version_combo.currentData()
        if release and QMessageBox.question(self, "确认回滚", f"将程序切换到 {release['tag']}？") == QMessageBox.StandardButton.Yes:
            self._install_release(release)

    def _install_release(self, release: dict) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(self, "仅支持打包版本", "请运行打包后的 kwtool.exe 来安装版本。")
            return
        if self._running:
            QMessageBox.information(self, "查询进行中", "请等待当前查询结束后再更新。")
            return
        self.update_button.setEnabled(False)
        self.rollback_button.setEnabled(False)
        self.update_button.setText("下载中…")
        self._download_worker = DownloadWorker(release, Path(sys.executable).resolve().parent)
        self._download_worker.ready.connect(self._apply_release)
        self._download_worker.failed.connect(self._update_failed)
        self._download_worker.start()

    def _apply_release(self, replacement: Path) -> None:
        try:
            launch_swap(replacement)
        except Exception as exc:
            self._update_failed(str(exc))
            return
        self.close()

    def _open_output_directory(self) -> None:
        try:
            template = validate_template(self.template_edit.text())
            subjects = [line.strip() for line in self.subjects_edit.toPlainText().splitlines() if line.strip()]
            adapter = self._adapters.get(self.site_combo.currentData())
            website = adapter.display_name if adapter else "网站"
            directory = OutputPlan(self._output_base, template, datetime.now()).render(
                1, subjects[0] if subjects else "未命名", website
            ).parent
            directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                raise OSError(f"无法打开目录：{directory}")
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法打开输出目录", str(exc))

    def _start_batch(self) -> None:
        try:
            subjects = tuple(line.strip() for line in self.subjects_edit.toPlainText().splitlines() if line.strip())
            if not subjects:
                raise ValueError("请至少输入一个待查询主体")
            template = validate_template(self.template_edit.text())
            key = self.site_combo.currentData()
            if key not in self._adapters:
                raise ValueError("请选择网站")
            self._capture_site_fields()
            request = BatchRequest(
                site_key=key,
                subjects=subjects,
                output_template=template,
                pdf=self._pdf_settings(),
                site_settings=dict(self._site_values.get(key, {})),
                diagnostics=self.developer_mode_check.isChecked(),
            )
            self._save_preferences()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法开始查询", str(exc))
            return
        self._running = True
        self._last_row_error = ""
        self._mail_summary = ""
        self._total = len(request.subjects)
        self.progress_bar.setRange(0, self._total)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._log(f"开始批次：{len(request.subjects)} 个主体")
        if not self._worker.isRunning():
            self._create_worker()
            self._worker.submit(request)
            self._worker.start()
        else:
            self._worker.submit(request)

    def _stop_batch(self) -> None:
        self._worker.stop_batch()
        self.stop_button.setEnabled(False)
        self.status_label.setText("正在停止，等待当前浏览器操作结束…")
        self._log("已请求停止")

    def _on_browser_status(self, ready: bool, message: str) -> None:
        self._browser_error = "" if ready else message
        if not self._running:
            self._set_user_action(not ready)
        self._log(message)
        if not self._running:
            self.status_label.setText(message)
        if not ready and not self._running:
            self.run_button.setEnabled(True)

    def _on_batch_state(self, state: str) -> None:
        self._log(f"批次状态：{state}")

    def _on_waiting(self, message: str) -> None:
        self.status_label.setText(message)
        self._log(message)

    def _set_user_action(self, needed: bool) -> None:
        color = "#e24b4b" if needed else "#36b76c"
        self.status_light.setStyleSheet(f"background-color: {color}; border-radius: 6px;")
        self.status_light.setToolTip("需要用户操作" if needed else "无需用户操作")

    def _on_row_result(self, index: int, subject: str, success: bool, detail: str) -> None:
        self.progress_bar.setValue(index)
        mark = "完成" if success else "失败"
        if not success:
            self._last_row_error = f"{index:03d} {subject}：{detail}"
        self._log(f"{index:03d} {subject}：{mark}。{detail}")

    def _on_batch_result(self, successes: int, failures: int, stopped: bool) -> None:
        self._running = False
        self.progress_bar.hide()
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        summary = format_batch_summary(successes, failures, stopped, self._total, self._last_row_error)
        if self._mail_summary:
            summary += "\n" + self._mail_summary
        self._set_user_action(bool(self._browser_error))
        self.status_label.setText(self._browser_error if stopped and successes == failures == 0 and self._browser_error else summary)
        self._log(summary)

    def _on_mail_result(self, saved: int, unresolved: int, detail: str) -> None:
        self._mail_summary = f"163 邮箱报告：已保存 {saved}，未保存 {unresolved}"
        if detail:
            self._mail_summary += f"；{detail}"
        self._log(self._mail_summary)

    def _log(self, message: str) -> None:
        self._logger.info(message)
        if self._running:
            self.status_label.setText(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt method name
        if hasattr(self, "_download_worker") and self._download_worker.isRunning():
            QMessageBox.information(self, "正在下载", "请等待版本下载完成后关闭程序。")
            event.ignore()
            return
        if not self._closing:
            try:
                self._save_preferences()
            except OSError as exc:
                QMessageBox.warning(self, "设置未保存", str(exc))
            self._closing = True
        if self._worker.isRunning():
            self._worker.shutdown()
            self.status_label.setText("正在关闭 Chrome…")
            if not self._close_connected:
                self._worker.finished.connect(self.close)
                self._close_connected = True
            event.ignore()
            return
        event.accept()
