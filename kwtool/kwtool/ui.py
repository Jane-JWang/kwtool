from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from kwtool.adapters import registered_adapters
from kwtool.models import BatchRequest, BrowserLayout, FieldSpec, PdfSettings
from kwtool.output import validate_template
from kwtool.preferences import default_template, load_settings, profile_path, save_settings, settings_path
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
        self._output_base = documents_base()
        log_path = settings_path().parent / "kwtool.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8"):
                pass
        except OSError:
            log_path = Path(tempfile.gettempdir()) / "kwtool" / "kwtool.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("kwtool")
        self._logger.setLevel(logging.INFO)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.addHandler(handler)
        self._total = 0
        self._layout_windows()
        self._build_ui()
        self._restore_settings()

        self._create_worker()
        self._worker.start()

    def _create_worker(self) -> None:
        self._worker = BrowserWorker(profile_path(), self._output_base, self._browser_layout)
        self._worker.browser_status.connect(self._on_browser_status)
        self._worker.batch_state.connect(self._on_batch_state)
        self._worker.progress.connect(self._log)
        self._worker.row_result.connect(self._on_row_result)
        self._worker.batch_result.connect(self._on_batch_result)

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
        self.subjects_edit.setPlaceholderText("每行输入一个主体名称")
        self.subjects_edit.setFixedHeight(110)
        upper_layout.addWidget(self.subjects_edit)

        template_header = QHBoxLayout()
        template_header.addWidget(QLabel("输出路径模板"))
        help_button = QToolButton()
        help_button.setText("ⓘ")
        help_button.setAccessibleName("输出路径模板说明")
        help_text = "[id] - 批次序号\n[object] - 查询主体\n[website] - 网站名称\n[date] - 日期 YYMMDD\n[time] - 时间 HHMMSS\n[year] - 年\n[month] - 月\n[day] - 日\n[hour] - 小时\n[minute] - 分钟\n[second] - 秒"
        help_button.setToolTip(help_text)
        help_button.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
        help_button.clicked.connect(
            lambda: QToolTip.showText(help_button.mapToGlobal(help_button.rect().bottomLeft()), help_text, help_button)
        )
        template_header.addWidget(help_button)
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

        self.status_label = QLabel("正在启动 Chrome…")
        self.status_label.setWordWrap(True)
        upper_layout.addWidget(self.status_label)
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
        saved_key = self._saved.get("site_key")
        index = self.site_combo.findData(saved_key)
        if index >= 0:
            self.site_combo.setCurrentIndex(index)
        self._render_site_fields()

    def _read_field(self, spec: FieldSpec, widget: QWidget) -> bool | str | int:
        if spec.kind == "bool":
            return widget.isChecked()  # type: ignore[attr-defined]
        if spec.kind == "integer":
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
                widget.setRange(-1_000_000, 1_000_000)
                try:
                    widget.setValue(int(value))
                except (TypeError, ValueError):
                    widget.setValue(int(spec.default))
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
        })

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
            )
            self._save_preferences()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法开始查询", str(exc))
            return
        self._running = True
        self._last_row_error = ""
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
        self.status_label.setText(self._browser_error if stopped and successes == failures == 0 and self._browser_error else summary)
        self._log(summary)

    def _log(self, message: str) -> None:
        self._logger.info(message)
        if self._running:
            self.status_label.setText(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt method name
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
