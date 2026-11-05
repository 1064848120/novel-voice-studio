"""PySide6 desktop user interface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

try:
    from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
        QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QPushButton, QSplitter, QStackedWidget, QTabWidget, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog, QProgressBar,
        QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QScrollArea, QSpinBox, QMenu,
    )
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError as exc:  # Keep importing project/core helpers possible without GUI packages.
    if __name__ == "__main__":
        raise SystemExit("尚未安装 PySide6。请在项目目录运行：python -m pip install -r requirements.txt") from exc
    raise

from .task_ui import TaskDialog, ReviewDialog
from .review_assist import complete_chapter
from .recent_projects import remove_project
from .api_analysis import APINovelAnalyzer, AnalysisCancelled
from .credentials import protect_key, unprotect_key
from .casting import load_library, save_library, import_voice, auto_cast, merge_characters, bind_library_voice, resolve_speakers
from .voice_ui import edit_traits
from .voice_batch import design_batch
from .workflow import (ensure_workflow, update_statistics, analyze_batch, review_chapter,
                       is_reviewed, approve_pilot, pilot_ready, profile_for, chapter_issues)
from .breeze_render import render_queue, apply_results, collect_jobs, file_hash
from .mastering import export_chapter_audio, export_m4b
from .audio import export_mp3, mix_layers, process_voice, read_wav, resample, write_wav
from .engines import (
    detect_device, index_model_revision,
    validate_index_model,
)
from .project import add_character, bind_reference_audio, create_project, export_is_allowed, load_project, save_project
from .quality import compare_voice, discover_asr
from .sound_design import generate_preview_library, recommend_cues
from .importers import repair_analyzed_segments
from .auto_production import run_auto_production


APP_NAME = "声章 · 小说讲书工作台"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "NovelVoiceStudio"
PROJECTS_DIR = Path.home() / "Documents" / "小说讲书"
KNOWN_INDEX_MODEL_DIR = Path(r"F:\VoiceoverBuild-20261104\models\indextts-2.5")
KNOWN_INDEX_PYTHON = Path(r"C:\Users\10648\Documents\Codex\2026-11-04\x20-f-comfyui-aki-motiongit-template\work\indextts-runtime\.venv\Scripts\python.exe")

STYLES = """
QMainWindow { background: #f5f7f8; color: #16191b; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 13px; color: #202326; }
QFrame#sidebar { background: #eef3f4; border: none; }
QFrame#sheet { background: white; border: 1px solid #e2e6e8; border-radius: 14px; }
QLabel#brand { font-size: 17px; font-weight: 600; color: #153d46; }
QLabel#pageTitle { font-size: 22px; font-weight: 600; }
QLabel#muted { color: #62696c; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QPushButton { background: #fff; border: 1px solid #d4d9dc; border-radius: 6px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { border-color: #1b6c80; background: #f7fbfc; }
QPushButton#primary { background: #171a1c; color: white; border-color: #171a1c; font-weight: 600; }
QPushButton#primary:hover { background: #2d3437; }
QPushButton#nav { text-align: left; border: none; background: transparent; padding: 10px 13px; color: #4e5b5f; }
QPushButton#nav:checked { color: #102a30; border-bottom: 2px solid #1b6c80; font-weight: 600; }
QPushButton#quiet { border: none; background: transparent; text-align: left; }
QLineEdit, QComboBox, QDoubleSpinBox { background: white; border: 1px solid #dfe3e5; border-radius: 5px; padding: 6px 8px; min-height: 22px; }
QTableWidget { background: white; alternate-background-color: #fff; border: none; gridline-color: #edf0f1; selection-background-color: #eaf3f5; selection-color: #151819; }
QHeaderView::section { background: white; border: none; border-bottom: 1px solid #e2e6e8; color: #687074; padding: 7px; font-weight: 500; }
QListWidget { border: none; background: transparent; outline: none; }
QListWidget::item { padding: 9px 8px; border-bottom: 1px solid #f0f2f3; }
QListWidget::item:selected { background: #ebedee; color: #16191b; border-radius: 5px; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 9px 14px; color: #5d6467; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #1b454f; border-bottom-color: #1b6c80; font-weight: 600; }
QProgressBar { height: 20px; border: 0; border-radius: 4px; background: #edf0f1; text-align: center; }
QProgressBar::chunk { border-radius: 4px; background: #1b6c80; }
QGroupBox { border: 1px solid #e6e9eb; border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: 600; }
QLabel#taskFeedback { background: #eef3f4; padding: 12px; border-radius: 6px; color: #153d46; }
QTabWidget#workflowSteps QTabBar::tab { padding: 12px 16px; font-weight: 600; }
QTabWidget#workflowSteps QTabBar::tab:selected { background: #eef3f4; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
"""


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    status = Signal(object)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.succeeded.emit(self.function(self.status.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


def _settings() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path = APP_DIR / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    (APP_DIR / "settings.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(1120, 700)
        self.setStyleSheet(STYLES)
        self.project_path: Path | None = None
        self.project: dict | None = None
        self.current_chapter = 0
        self._updating_table = False
        self._worker: Worker | None = None
        self.task_dialog = None
        self.previewed_segment: tuple[str, int] | None = None
        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.settings = _settings()
        self.cancel_event = threading.Event()
        self.device = detect_device()
        self.models_dir = Path(self.settings.get("models_dir", APP_DIR / "models"))
        default_index_model = KNOWN_INDEX_MODEL_DIR if KNOWN_INDEX_MODEL_DIR.is_dir() else self.models_dir / "index-tts-2.5"
        default_index_python = KNOWN_INDEX_PYTHON if KNOWN_INDEX_PYTHON.is_file() else Path(sys.executable)
        self.index_model_dir = Path(self.settings.get("index_model_dir", default_index_model)).expanduser()
        self.settings.setdefault("index_model_dir", str(self.index_model_dir))
        self.settings.setdefault("indextts_python", str(default_index_python))
        self.tts_engine = None
        self._build()
        self._refresh_engine_status()
        self.refresh_workflow()

    def _build(self):
        outer = QWidget()
        root = QHBoxLayout(outer)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 14, 12, 12)
        brand = QLabel("声章")
        brand.setObjectName("brand")
        side.addWidget(brand)
        sub = QLabel("小说讲书工作台")
        sub.setObjectName("muted")
        side.addWidget(sub)
        side.addSpacing(24)
        self.new_project_button = QPushButton("＋  导入小说")
        self.new_project_button.setObjectName("nav")
        self.new_project_button.clicked.connect(self.import_novel)
        side.addWidget(self.new_project_button)
        side.addSpacing(15)
        for label, slot in (("制作工作台", lambda: self.pages.setCurrentIndex(0)), ("声音与模型", lambda: self.pages.setCurrentIndex(1)), ("音色库", lambda: self.pages.setCurrentIndex(2))):
            button = QPushButton(label)
            button.setObjectName("nav")
            button.clicked.connect(slot)
            side.addWidget(button)
        side.addSpacing(8)
        recent = QLabel("最近项目")
        recent.setObjectName("muted")
        side.addWidget(recent)
        self.project_list = QListWidget()
        self.project_list.itemClicked.connect(self._open_project_item)
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.recent_menu)
        side.addWidget(self.project_list, 1)
        delete_project = QPushButton('删除所选项目')
        delete_project.clicked.connect(self.delete_recent_project)
        side.addWidget(delete_project)
        side.addWidget(QLabel("本地项目 · 文件保存在此电脑"))

        self.pages = QStackedWidget()
        self.home_page = self._make_home()
        self.settings_page = self._make_settings()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self._make_library_page())
        root.addWidget(sidebar)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(outer)
        self._load_recent()
        self.stop_button = QPushButton('停止任务')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_task)
        self.statusBar().addPermanentWidget(self.stop_button)

    def _page_shell(self, title: str, subtitle: str = ""):
        page = QFrame()
        page.setObjectName("sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("pageTitle")
        titles.addWidget(label)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("muted")
            titles.addWidget(hint)
        header.addLayout(titles, 1)
        layout.addLayout(header)
        return page, layout, header

    def _make_home(self):
        page, layout, header = self._page_shell("制作工作台", "导入小说，选择范围，点击开始自动制作；只有异常时需要处理。")
        self.project_title = QLabel("还没有打开项目")
        self.project_title.setObjectName("sectionTitle")
        header.addWidget(self.project_title)
        self.device_label = QLabel()
        self.device_label.setObjectName("muted")
        header.addWidget(self.device_label)

        # Hierarchy §2.1/§2.8: one active numbered stage, optional tools folded away.
        # Existing tokens: 12/16/24 spacing, 13/15/22 type, white/teal/charcoal.
        self.project_controls = QWidget()
        controls = QVBoxLayout(self.project_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(12)
        self.workflow_status = QLabel('从第 1 步开始：导入小说，然后分析人物与对白。')
        self.workflow_status.setWordWrap(True)
        self.workflow_status.setObjectName('sectionTitle')
        controls.addWidget(self.workflow_status)
        auto_row = QHBoxLayout()
        self.auto_button = QPushButton('开始自动制作')
        self.auto_button.setObjectName('primary')
        self.auto_button.clicked.connect(self.start_auto_production)
        auto_row.addWidget(self.auto_button)
        self.auto_continue_button = QPushButton('继续制作')
        self.auto_continue_button.clicked.connect(self.start_auto_production)
        auto_row.addWidget(self.auto_continue_button)
        self.auto_issue_button = QPushButton('查看异常')
        self.auto_issue_button.clicked.connect(self.show_auto_issues)
        auto_row.addWidget(self.auto_issue_button)
        self.retry_issue_button = QPushButton('重新制作异常章节')
        self.retry_issue_button.clicked.connect(self.retry_auto_issues)
        auto_row.addWidget(self.retry_issue_button)
        self.redo_all_button = QPushButton('批量重新生成')
        self.redo_all_button.clicked.connect(self.redo_auto_range)
        auto_row.addWidget(self.redo_all_button)
        self.output_button = QPushButton('打开输出目录')
        self.output_button.clicked.connect(self.open_output_folder)
        auto_row.addWidget(self.output_button)
        self.advanced_button = QPushButton('高级设置')
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.toggle_advanced)
        auto_row.addWidget(self.advanced_button)
        auto_row.addStretch()
        controls.addLayout(auto_row)
        self.steps = QTabWidget()
        self.steps.setObjectName('workflowSteps')
        controls.addWidget(self.steps)
        def stage(title, hint):
            page = QWidget()
            box = QVBoxLayout(page)
            box.setContentsMargins(12, 12, 12, 12)
            box.setSpacing(12)
            note = QLabel(hint); note.setWordWrap(True)
            box.addWidget(note)
            actions = QHBoxLayout(); box.addLayout(actions)
            self.steps.addTab(page, title)
            return box, actions
        def action(row, title, handler, primary=False):
            button = QPushButton(title)
            if primary:
                button.setObjectName('primary')
            button.clicked.connect(handler)
            row.addWidget(button)
            return button
        box, row = stage('1  导入与分析', '第一次建议先跑通当前一章：分析 → 自动配声 → 校对 → 试播 → 导出。确认成功后，再用批量分析处理 30 章。接口未配置时，先到“声音与模型”填写 NewAPI。')
        action(row, '导入小说', self.import_novel)
        self.analyze_button = action(row, '① 先分析当前章（推荐）', self.analyze_chapter, True)
        self.batch_analyze_button = action(row, '批量分析 / 继续本批', self.analyze_next_batch)
        action(row, '配置 NewAPI', lambda: self.pages.setCurrentIndex(1))
        row.addStretch()
        options = QPushButton('批次设置与单章操作 ▸'); options.setCheckable(True)
        box.addWidget(options, alignment=Qt.AlignmentFlag.AlignLeft)
        advanced = QWidget(); advanced_row = QHBoxLayout(advanced); advanced_row.setContentsMargins(0,0,0,0)
        self.sample_size = QSpinBox(); self.sample_size.setRange(1, 100); self.sample_size.setValue(30)
        self.batch_size = QSpinBox(); self.batch_size.setRange(1, 50); self.batch_size.setValue(10)
        self.pilot_count = QSpinBox(); self.pilot_count.setRange(1, 2); self.pilot_count.setValue(2)
        for key, label, widget in [('sample_size', '首轮章数', self.sample_size), ('batch_size', '增量章数', self.batch_size), ('pilot_count', '试播章数', self.pilot_count)]:
            widget.valueChanged.connect(lambda value, k=key: self._set_workflow_option(k, value))
            advanced_row.addWidget(QLabel(label)); advanced_row.addWidget(widget)
        action(advanced_row, '仅分析当前章', self.analyze_chapter)
        advanced_row.addStretch()
        advanced.hide(); options.toggled.connect(advanced.setVisible); box.addWidget(advanced)
        box, row = stage('2  准备声音', '点击自动配声：已有音色自动匹配，缺少的自动设计并绑定人物台词。完成后只需试听确认，不必逐个选声。')
        action(row, '自动配声（匹配＋设计）', self.match_voices, True)
        action(row, '为待配声人物设计声音', self.design_pending_characters)
        action(row, '指定主角', self.select_protagonist)
        row.addStretch()
        box, row = stage('3  校对剧本', '在下方逐章检查说话人、情绪和停顿，再确认当前章。先校对试播的前 1～2 章。')
        action(row, '本章校对通过', self.approve_script, True)
        action(row, '查找问题 / AI 补全', self.show_review_issues)
        action(row, '下一章', self.next_chapter)
        row.addStretch()
        box, row = stage('4  试播与生成', '先生成试播 → 逐段播放与确认 → 确认试播通过 → 校对其余章节后生成当前批次。')
        action(row, '① 生成试播', self.render_pilot, True)
        action(row, '② 确认试播通过', self.confirm_pilot)
        action(row, '③ 生成当前批次 / 续做', self.render_batch)
        self.render_button = action(row, '仅生成当前章', self.render_chapter)
        row.addStretch()
        box, row = stage('5  音效与导出', '可先在“音效时间线”调整背景声，再逐章导出 WAV / MP3。已有章节母带后，可合成分章 M4B。')
        self.export_button = action(row, '导出本章 WAV / MP3', self.export_chapter, True)
        action(row, '编辑音效', lambda: self.editor_tabs.setCurrentIndex(1))
        action(row, '导出分章 M4B', self.export_book)
        action(row, '分析下一批 →', lambda: self.steps.setCurrentIndex(0))
        row.addStretch()
        self.steps.currentChanged.connect(self._step_changed)
        self.steps.setVisible(False)
        layout.addWidget(self.project_controls)
        self.task_feedback = QLabel('')
        self.task_feedback.setWordWrap(True)
        self.task_feedback.setObjectName('taskFeedback')
        self.task_feedback.hide()
        layout.addWidget(self.task_feedback)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(self._make_chapter_editor(), "正文与人物")
        self.editor_tabs.addTab(self._make_sound_editor(), "音效时间线")
        self.editor_tabs.setVisible(False)
        layout.addWidget(self.editor_tabs, 1)

        self.empty_panel = QFrame()
        self.empty_panel.setObjectName("sheet")
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(40, 54, 40, 54)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("把小说变成一场有声演出")
        empty_title.setObjectName("pageTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_text = QLabel("导入 TXT 或 EPUB，选择章节范围，点击开始自动制作。")
        empty_text.setObjectName("muted")
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_text)
        import_button = QPushButton("选择小说文件")
        import_button.setObjectName("primary")
        import_button.clicked.connect(self.import_novel)
        empty_layout.addWidget(import_button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_panel.show()
        self.editor_tabs.hide()
        layout.addWidget(self.empty_panel, 1)
        return page

    def toggle_advanced(self, visible):
        if hasattr(self, 'steps'):
            self.steps.setVisible(bool(visible))
        if hasattr(self, 'editor_tabs'):
            self.editor_tabs.setVisible(bool(visible))
        if hasattr(self, 'advanced_button'):
            self.advanced_button.setText('收起高级设置' if visible else '高级设置')

    def _make_chapter_editor(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        chapters_frame = QFrame()
        chapters_layout = QVBoxLayout(chapters_frame)
        title = QLabel("章节")
        title.setObjectName("sectionTitle")
        chapters_layout.addWidget(title)
        self.chapters_widget = QListWidget()
        self.chapters_widget.currentRowChanged.connect(self.select_chapter)
        chapters_layout.addWidget(self.chapters_widget, 1)
        split.addWidget(chapters_frame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(6, 0, 6, 0)
        caption = QHBoxLayout()
        self.chapter_heading = QLabel("请选择章节")
        self.chapter_heading.setObjectName("sectionTitle")
        self.segment_count = QLabel("")
        self.segment_count.setObjectName("muted")
        caption.addWidget(self.chapter_heading)
        caption.addStretch(1)
        caption.addWidget(self.segment_count)
        content_layout.addLayout(caption)
        self.segments_table = QTableWidget(0, 9)
        self.segments_table.setHorizontalHeaderLabels(["文本片段", "类型", "说话人", "情绪", "试听", "音色确认", "强度", "语速", "停顿秒"])
        self.segments_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 9):
            self.segments_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.segments_table.verticalHeader().setVisible(False)
        self.segments_table.setWordWrap(True)
        self.segments_table.itemChanged.connect(self._segment_text_changed)
        content_layout.addWidget(self.segments_table, 1)
        split.addWidget(content)

        cast = QFrame()
        cast.setMinimumWidth(230)
        cast_layout = QVBoxLayout(cast)
        cast_title_row = QHBoxLayout()
        cast_title = QLabel("人物音色")
        cast_title.setObjectName("sectionTitle")
        cast_title_row.addWidget(cast_title)
        add_button = QPushButton("＋ 添加")
        add_button.clicked.connect(self.add_person)
        cast_title_row.addWidget(add_button)
        cast_layout.addLayout(cast_title_row)
        narrator_group = QGroupBox("旁白")
        self.narrator_group = narrator_group
        narrator_layout = QVBoxLayout(narrator_group)
        self.narrator_status = QLabel("尚未绑定参考音频")
        self.narrator_status.setObjectName("muted")
        narrator_layout.addWidget(self.narrator_status)
        narrator_button = QPushButton("更换旁白音色（可选）")
        narrator_button.clicked.connect(lambda: self.bind_voice(None))
        narrator_layout.addWidget(narrator_button)
        for label, handler in [('试听声卡', self.preview_card), ('确认声音与文字稿', self.approve_card), ('重新设计声音', self.redesign_character)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, fn=handler: fn())
            narrator_layout.addWidget(button)
        cast_layout.addWidget(narrator_group)
        self.cast_list = QVBoxLayout()
        cast_layout.addLayout(self.cast_list)
        cast_layout.addStretch(1)
        cast_scroll = QScrollArea()
        self.cast_scroll = cast_scroll
        cast_scroll.setMinimumWidth(250)
        cast_scroll.setWidgetResizable(True)
        cast_scroll.setWidget(cast)
        split.addWidget(cast_scroll)
        split.setSizes([190, 760, 260])
        layout.addWidget(split, 1)
        return widget

    def _make_sound_editor(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        intro = QLabel("音效会根据剧情词句给出建议。拖入或导入你有权使用的 WAV 素材，再试听、调节音量并决定是否保留。")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        actions = QHBoxLayout()
        suggest = QPushButton("重新推荐本章音效")
        suggest.clicked.connect(self.refresh_cues)
        add_sound = QPushButton("导入音效 / 配乐")
        add_sound.clicked.connect(self.import_sound_asset)
        actions.addWidget(suggest)
        actions.addWidget(add_sound)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.cues_table = QTableWidget(0, 5)
        self.cues_table.setColumnCount(6)
        self.cues_table.setHorizontalHeaderLabels(["使用", "音效 / 氛围", "类型", "位置", "音量", "素材"])
        self.cues_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4, 5):
            self.cues_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.cues_table.verticalHeader().setVisible(False)
        layout.addWidget(self.cues_table, 1)
        self.sound_assets_label = QLabel("示例氛围素材会在首次打开项目时生成；可导入自有授权录音替换。")
        self.sound_assets_label.setObjectName("muted")
        layout.addWidget(self.sound_assets_label)
        self.reverb_checkbox = QCheckBox("轻量空间混响（适合室内对白；默认关闭）")
        self.reverb_checkbox.setChecked(False)
        self.reverb_checkbox.stateChanged.connect(lambda state: self._set_audio_setting("reverb", bool(state)))
        layout.addWidget(self.reverb_checkbox)
        self.loudness_combo = QComboBox()
        self.loudness_combo.addItem('单声道人声：-19 LUFS', -19)
        self.loudness_combo.addItem('响度较高：-16 LUFS', -16)
        self.loudness_combo.currentIndexChanged.connect(lambda _: self._set_audio_setting('lufs', self.loudness_combo.currentData()))
        layout.addWidget(self.loudness_combo)
        self.denoise_checkbox = QCheckBox('导出前降噪（默认关闭，合成音频通常无需降噪）')
        self.denoise_checkbox.toggled.connect(lambda v: self._set_audio_setting('denoise', v))
        layout.addWidget(self.denoise_checkbox)
        return widget

    def _make_library_page(self):
        page, layout, header = self._page_shell('音色库', '一次入库，按人物特征自动配声；配声完成后固定用于所有章节。')
        add = QPushButton('添加音色素材')
        add.clicked.connect(self.add_library_voice)
        header.addWidget(add)
        design = QPushButton('文字设计新音色')
        design.clicked.connect(self.design_library_voice)
        header.addWidget(design)
        self.library_table = QTableWidget(0, 7)
        self.library_table.setHorizontalHeaderLabels(['名称', '性别 / 年龄', '音色 / 性格', '用途', '试听', '编辑', '移出库'])
        self.library_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.library_table.verticalHeader().hide()
        layout.addWidget(self.library_table)
        self.library_hint = QLabel()
        self.library_hint.setWordWrap(True)
        layout.addWidget(self.library_hint)
        self._refresh_library()
        return page

    def _refresh_library(self):
        try:
            voices = load_library(APP_DIR / 'voice-library')
        except Exception as exc:
            self.library_hint.setText(str(exc))
            return
        self.library_table.setRowCount(len(voices))
        for row, voice in enumerate(voices):
            values = [voice['name'], f"{voice.get('gender', '未指定')} / {voice.get('age', '未指定')}",
                      f"{voice.get('timbre', '未指定')} · {voice.get('personality', '')}", voice.get('role', '角色')]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.library_table.setItem(row, col, item)
            for col, label, action in [(4, '播放', self.preview_library_voice), (5, '编辑档案', self.edit_library_voice), (6, '移出', self.remove_library_voice)]:
                button = QPushButton(label)
                button.clicked.connect(lambda _=False, v=voice, fn=action: fn(v))
                self.library_table.setCellWidget(row, col, button)
        self.library_hint.setText(f'共 {len(voices)} 个音色。每个人物自动分配独立音色；缺少合适声音时保持待配声。' if voices else
                                 '音色库为空。点击“文字设计新音色”，描述年龄、性别、声线与性格即可生成；也可导入已有 WAV。小说分析后，可批量为待配声人物设计声音，无需逐个上传参考音频。')

    def add_library_voice(self):
        path, _ = QFileDialog.getOpenFileName(self, '添加可重复使用的音色', str(Path.home()), 'WAV (*.wav)')
        if not path:
            return
        traits = edit_traits(self, {'name': Path(path).stem}, asset=True)
        if traits is None:
            return
        try:
            import_voice(APP_DIR / 'voice-library', path, traits)
            self._refresh_library()
        except Exception as exc:
            QMessageBox.warning(self, '音色入库失败', str(exc))

    def edit_library_voice(self, voice):
        traits = edit_traits(self, voice, asset=True)
        if traits is not None:
            voices = load_library(APP_DIR / 'voice-library')
            next(v for v in voices if v['id'] == voice['id']).update(traits)
            save_library(APP_DIR / 'voice-library', voices)
            self._refresh_library()

    def remove_library_voice(self, voice):
        voices = load_library(APP_DIR / 'voice-library')
        save_library(APP_DIR / 'voice-library', [v for v in voices if v['id'] != voice['id']])
        self._refresh_library()
        self.statusBar().showMessage('已从可选音色中移出；项目中固定的声音仍保留。', 8000)

    def preview_library_voice(self, voice):
        path = APP_DIR / 'voice-library' / voice['file']
        self.audio_player.setSource(QUrl.fromLocalFile(str(path)))
        self.audio_player.play()

    def match_voices(self, checked=False, show_result=True):
        if not self.project:
            return
        try:
            assigned, pending = auto_cast(self.project, self.project_path, APP_DIR / 'voice-library')
            self._save_project()
            self._refresh_cast()
            self._populate_segments()
            self.refresh_workflow()
            message = f'新分配 {len(assigned)} 个声音，已有角色保持原绑定。'
            if pending:
                message += '\n待配声：\n' + '\n'.join(pending)
            if show_result and pending:
                self.design_pending_characters()
            elif show_result:
                QMessageBox.information(self, '人物配声', message + '\n已按说话人自动关联台词，无需逐句绑定。请试听确认。')
            return message
        except Exception as exc:
            self._save_project()
            QMessageBox.warning(self, '配声未完成', str(exc))
            return '请检查音色库。'

    def edit_character_traits(self, character):
        values = edit_traits(self, character)
        if values is not None:
            character.update(values)
            character['traits_locked'] = True
            self._save_project()
            self._refresh_cast()

    def save_breeze_settings(self):
        for key, widget in self.breeze_paths.items():
            self.settings[key] = widget.text().strip()
        self.settings['asr_model'] = self.asr_path.text().strip()
        _save_settings(self.settings)
        self.statusBar().showMessage('Breeze 路径已保存。', 5000)

    def design_library_voice(self):
        traits = edit_traits(self, {'name': '新设计音色'}, asset=True)
        if traits is not None:
            self._start_voice_design([(None, traits)])

    def design_pending_characters(self):
        if not self.project:
            return
        jobs = [(c['id'], {**{k: c.get(k, '') for k in ('name', 'gender', 'age', 'timbre', 'personality', 'description')}, 'role': '角色'})
                for c in self.project['characters'] if not c['voice_profile'].get('reference_audio')]
        if not self.project.get('narrator_voice_profile', {}).get('reference_audio'):
            jobs.insert(0, ('narrator', {'name': '故事旁白', 'role': '旁白', 'gender': '未指定', 'age': '中年',
                                       'timbre': '沉稳', 'personality': '清晰自然，适合长篇讲述'}))
        if not jobs:
            QMessageBox.information(self, '声音已固定', '当前人物和旁白已有声音，后续章节会继续复用。')
            return
        self._start_voice_design(jobs)

    def _start_voice_design(self, jobs):
        self.save_breeze_settings()
        config = {key: widget.text().strip() for key, widget in self.breeze_paths.items()}
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False)) if self.project else None
        folder = self.project_path
        self._run_worker(lambda status: design_batch(snapshot, folder, APP_DIR, jobs, config, status, self.cancel_event),
                         self._voice_design_done, title=f'第 2 步 · 设计 {len(jobs)} 个声音', cancelable=True)

    def _voice_design_done(self, result):
        if result.get('project') is not None:
            self.project = result['project']
        self._refresh_library()
        if self.project:
            self._refresh_cast()
            self._populate_segments()
            self.refresh_workflow()
        QMessageBox.information(self, '声音设计', result['message'] + '\n声音已自动绑定；如自动质检发现异常，可在“查看异常”中重做。')

    def redesign_character(self, character=None):
        if not self.project:
            return
        person = character or {'name': '故事旁白', 'id': 'narrator', 'age': '中年', 'timbre': '沉稳'}
        traits = edit_traits(self, person)
        if traits is None:
            return
        # Explicit redesign keeps existing audio until a new asset is successfully saved.
        traits.update(name=person['name'], role='旁白' if character is None else '角色')
        self._start_voice_design([(person['id'], traits)])

    def _make_settings(self):
        page, layout, _ = self._page_shell("声音与模型", "NewAPI 分析文本，Breeze 设计声音并直接生成对白；复用已安装模型。")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QVBoxLayout(inner)

        device_group = QGroupBox("运行设备")
        device_layout = QVBoxLayout(device_group)
        self.device_details = QLabel()
        self.device_details.setWordWrap(True)
        device_layout.addWidget(self.device_details)
        form.addWidget(device_group)

        models_group = QGroupBox("本地模型")
        models_layout = QVBoxLayout(models_group)
        self.model_status_label = QLabel()
        self.model_status_label.setWordWrap(True)
        models_layout.addWidget(self.model_status_label)
        index_model_row = QHBoxLayout()
        self.index_model_path = QLineEdit(str(self.index_model_dir))
        self.index_model_path.setPlaceholderText("选择已安装的 IndexTTS 2.5 模型目录")
        index_model_row.addWidget(self.index_model_path, 1)
        browse_model = QPushButton("选择模型目录")
        browse_model.clicked.connect(self.choose_index_model_dir)
        index_model_row.addWidget(browse_model)
        models_layout.addLayout(index_model_row)
        form.addWidget(models_group)

        api_group = QGroupBox("文本分析 · NewAPI")
        api_form = QFormLayout(api_group)
        self.api_url = QLineEdit(self.settings.get("analysis_api_url", ""))
        self.api_url.setPlaceholderText("https://你的 NewAPI 域名/v1")
        self.api_model = QLineEdit(self.settings.get("analysis_api_model", ""))
        self.api_model.setPlaceholderText("填写 NewAPI 中可用的模型名称")
        self.api_key = QLineEdit()
        try:
            self.api_key.setText(unprotect_key(self.settings.get("analysis_api_key_protected", "")))
        except Exception:
            self.statusBar().showMessage("已保存的密钥无法解密，请重新填写密钥并保存。", 15000)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("保存后使用 Windows 加密保留，重启自动读取")
        api_form.addRow("接口地址", self.api_url)
        api_form.addRow("模型名称", self.api_model)
        api_form.addRow("API 密钥", self.api_key)
        api_note = QLabel("分析时会将章节文本和人物档案发送到你填写的 NewAPI 服务；配音使用本机 Breeze。")
        api_note.setWordWrap(True)
        api_form.addRow(api_note)
        api_actions = QHBoxLayout()
        save_api = QPushButton("保存接口设置")
        save_api.clicked.connect(self.save_api_settings)
        test_api = QPushButton("测试连接")
        test_api.clicked.connect(self.test_api_connection)
        api_actions.addWidget(save_api)
        api_actions.addWidget(test_api)
        api_form.addRow(api_actions)
        form.addWidget(api_group)

        breeze_group = QGroupBox('Breeze TTS · 声音设计与对白生成')
        breeze_form = QFormLayout(breeze_group)
        project_root = Path(__file__).resolve().parent.parent
        self.breeze_paths = {}
        for key, label, default in [
            ('breeze_python', '运行环境', project_root / 'runtimes/breeze-env/Scripts/python.exe'),
            ('breeze_runtime', '程序目录', project_root / 'runtimes/breeze-tts'),
            ('breeze_models', '模型目录', project_root / 'models/breeze-tts-2'),
        ]:
            field = QLineEdit(self.settings.get(key, str(default)))
            self.breeze_paths[key] = field
            breeze_form.addRow(label, field)
        note = QLabel('Breeze 按人物描述设计固定声卡，再使用同一模型的 Voice Direction 表达喜怒哀乐。无需自行准备参考音频。Breeze 本地生成结果限研究、非商业使用。')
        note.setWordWrap(True)
        breeze_form.addRow(note)
        save_breeze = QPushButton('保存 Breeze 设置')
        save_breeze.clicked.connect(self.save_breeze_settings)
        breeze_form.addRow(save_breeze)
        self.asr_path = QLineEdit(self.settings.get('asr_model', discover_asr()))
        self.asr_path.setPlaceholderText('可选：已有 faster-whisper 模型目录；未配置则要求人工检查漏读')
        breeze_form.addRow('转写质检模型', self.asr_path)
        form.addWidget(breeze_group)

        runtime_group = QGroupBox("已有 IndexTTS 环境（保留配置）")
        runtime_layout = QVBoxLayout(runtime_group)
        runtime_layout.addWidget(QLabel("保留本机已有 IndexTTS 路径；当前小说生产流程固定使用 Breeze，不自动切换引擎。"))
        runtime_row = QHBoxLayout()
        self.tts_python = QLineEdit(self.settings.get("indextts_python", sys.executable))
        self.tts_python.setPlaceholderText("IndexTTS 环境中的 python.exe")
        runtime_row.addWidget(self.tts_python, 1)
        browse_runtime = QPushButton("选择 python.exe")
        browse_runtime.clicked.connect(self.choose_tts_python)
        runtime_row.addWidget(browse_runtime)
        runtime_layout.addLayout(runtime_row)
        save_runtime = QPushButton("保存并检查环境")
        save_runtime.clicked.connect(self.save_tts_runtime)
        runtime_layout.addWidget(save_runtime)
        form.addWidget(runtime_group)

        install_group = QGroupBox("运行组件")
        install_layout = QVBoxLayout(install_group)
        install_layout.addWidget(QLabel("Breeze 生成需要约 8.5GB 空闲显存，请先关闭其他占用显存的模型服务。不会重复安装 IndexTTS。"))
        install_layout.addWidget(QLabel("文本分析通过 NewAPI 调用，无需下载本地文本模型。未配置接口时，可导入小说并手动校对。"))
        form.addWidget(install_group)
        form.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 0)
        self.download_progress.hide()
        layout.addWidget(self.download_progress)
        self.download_status = QLabel("")
        self.download_status.setObjectName("muted")
        layout.addWidget(self.download_status)
        return page

    def _refresh_engine_status(self):
        gpu = f"{self.device.gpu_name} · {self.device.vram_mb} MB" if self.device.nvidia_available else "未检测到 NVIDIA GPU"
        self.device_label.setText(gpu)
        self.device_details.setText(self.device.message + (f"\n显卡：{gpu}" if self.device.nvidia_available else "\n可继续导入和校对；本地 Breeze 需要 NVIDIA 显卡。"))
        model_text = "文本分析：NewAPI 接口\n配音：Breeze TTS 2 · 固定声卡 Voice Direction"
        index_dir = Path(self.index_model_path.text().strip() or self.index_model_dir).expanduser()
        try:
            validate_index_model(index_dir)
            revision = index_model_revision(index_dir)
            model_text += f"\nIndexTTS 2.5：已就绪（版本 {revision[:12]}）"
        except Exception as exc:
            model_text += f"\nIndexTTS 2.5：未就绪（{exc}）"
        python_path = Path(self.tts_python.text().strip() or "")
        model_text += f"\nIndexTTS Python 环境：{'已选择' if python_path.is_file() else '路径无效'}"
        self.model_status_label.setText(model_text)

    def _load_recent(self):
        self.project_list.clear()
        try:
            for path in sorted(PROJECTS_DIR.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
                if str(path.parent.resolve()) in self.settings.get('hidden_projects', []):
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                item = QListWidgetItem(data.get("title", path.parent.name))
                item.setData(Qt.ItemDataRole.UserRole, str(path.parent))
                self.project_list.addItem(item)
        except Exception:
            pass

    def recent_menu(self, position):
        item = self.project_list.itemAt(position)
        if not item:
            return
        self.project_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('删除项目（可恢复）', self.delete_recent_project)
        menu.addAction('仅从最近列表移除', self.hide_recent_project)
        menu.exec(self.project_list.mapToGlobal(position))

    def hide_recent_project(self):
        item = self.project_list.currentItem()
        if item:
            self.settings.setdefault('hidden_projects', []).append(str(Path(item.data(Qt.ItemDataRole.UserRole)).resolve()))
            _save_settings(self.settings)
            self._load_recent()

    def delete_recent_project(self):
        item = self.project_list.currentItem()
        if not item:
            QMessageBox.information(self, '选择项目', '请先选择要删除的最近项目。')
            return
        folder = Path(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(self, '删除项目', f'删除“{item.text()}”？\n项目和配音将移入项目回收目录，可恢复；原始小说文件不受影响。\n{folder}',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            target = remove_project(folder, PROJECTS_DIR)
            if self.project_path and folder.resolve() == self.project_path.resolve():
                self.project = None; self.project_path = None
                self.chapters_widget.clear()
                self.editor_tabs.hide(); self.empty_panel.show()
                self.project_title.setText('还没有打开项目')
                self.steps.setCurrentIndex(0)
                self.workflow_status.setText('从第 1 步开始：导入小说，然后分析人物与对白。')
                self.refresh_workflow()
            self._load_recent()
            QMessageBox.information(self, '项目已删除', f'已从最近项目移除。恢复时可将此文件夹移回上一级项目目录：\n{target}')
        except Exception as exc:
            QMessageBox.warning(self, '删除失败', str(exc))

    def _open_project_item(self, item):
        self.open_project(item.data(Qt.ItemDataRole.UserRole))

    def import_novel(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入小说", str(Path.home()), "小说文件 (*.txt *.epub)")
        if not path:
            return
        def import_file(status):
            status('正在读取小说并拆分章节，大文件需要稍等…')
            return create_project(path, PROJECTS_DIR)
        self._run_worker(import_file, self.open_project, title='第 1 步 · 导入小说')

    def start_auto_production(self):
        if not self.project:
            self.import_novel()
            return
        total = len(self.project.get('chapters', []))
        choices = ['全书', '当前章节', '自定义章节范围']
        choice, ok = QInputDialog.getItem(self, '开始自动制作', '选择制作范围', choices, 0, False)
        if not ok:
            return
        if choice == '全书':
            start, end = 0, total
        elif choice == '当前章节':
            start, end = self.current_chapter, self.current_chapter + 1
        else:
            start, ok = QInputDialog.getInt(self, '制作范围', '起始章节（从 1 开始）', 1, 1, total)
            if not ok:
                return
            end, ok = QInputDialog.getInt(self, '制作范围', '结束章节（包含）', min(total, start), start, total)
            if not ok:
                return
            start -= 1
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            self.pages.setCurrentIndex(1)
            QMessageBox.warning(self, '请配置 NewAPI', str(exc))
            return
        if not self.save_api_settings():
            return
        self.save_breeze_settings()
        config = {k: v.text().strip() for k, v in self.breeze_paths.items()}
        config['asr_model'] = self.asr_path.text().strip()
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        self._run_worker(lambda status: run_auto_production(snapshot, self.project_path, start, end, analyzer,
                                                            config, APP_DIR, status, self.cancel_event),
                         self.auto_production_done, self.auto_button,
                         title='自动制作小说', cancelable=True)

    def auto_production_done(self, result):
        self.project = load_project(self.project_path)
        self._refresh_chapter_list(); self._populate_segments(); self._refresh_cast(); self.refresh_workflow()
        issues = result.get('issues', []) if isinstance(result, dict) else []
        self.task_feedback.setText(result.get('message', '自动制作完成。') if isinstance(result, dict) else '自动制作完成。')
        self.task_feedback.show()
        if issues:
            QMessageBox.warning(self, '自动制作完成，但有异常', self._format_auto_issues(issues))
        else:
            QMessageBox.information(self, '自动制作完成', self.task_feedback.text() + '\n章节 WAV / MP3 已自动导出。')

    def _format_auto_issues(self, issues):
        return '\n'.join('• ' + str(item.get('message', item)) for item in issues[:20])

    def show_auto_issues(self):
        if not self.project:
            return
        issues = []
        for index, chapter in enumerate(self.project.get('chapters', [])):
            for issue in chapter.get('production_issues', []):
                issues.append({'message': f'第 {index + 1} 章：{issue}'})
        if not issues:
            QMessageBox.information(self, '没有异常', '当前项目没有待处理异常。')
        else:
            QMessageBox.warning(self, '需要处理的异常', self._format_auto_issues(issues))

    def open_output_folder(self):
        if not self.project_path:
            return
        output = self.project_path / 'exports'
        output.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(output))
        except AttributeError:
            subprocess.Popen(['explorer', str(output)])

    def retry_auto_issues(self):
        if not self.project:
            return
        failed = [i for i, chapter in enumerate(self.project.get('chapters', []))
                  if chapter.get('production_state') == '有异常']
        if not failed:
            QMessageBox.information(self, '没有异常章节', '当前没有需要重新制作的章节。')
            return
        self._start_auto_range(min(failed), max(failed) + 1)

    def redo_auto_range(self):
        if not self.project:
            return
        total = len(self.project.get('chapters', []))
        start, ok = QInputDialog.getInt(self, '批量重新生成', '起始章节（从 1 开始）', 1, 1, total)
        if not ok:
            return
        end, ok = QInputDialog.getInt(self, '批量重新生成', '结束章节（包含）', min(total, start), start, total)
        if not ok:
            return
        for chapter in self.project['chapters'][start - 1:end]:
            chapter['production_state'] = '待分析'
            chapter.pop('production_signature', None)
            chapter['audio_path'] = None
            chapter['script_approval'] = None
            for segment in chapter.get('segments', []):
                self._invalidate_segment(segment)
        self._save_project()
        self._start_auto_range(start - 1, end)

    def _start_auto_range(self, start, end):
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            self.pages.setCurrentIndex(1)
            QMessageBox.warning(self, '请配置 NewAPI', str(exc))
            return
        if not self.save_api_settings():
            return
        self.save_breeze_settings()
        config = {k: v.text().strip() for k, v in self.breeze_paths.items()}
        config['asr_model'] = self.asr_path.text().strip()
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        self._run_worker(lambda status: run_auto_production(snapshot, self.project_path, start, end, analyzer,
                                                            config, APP_DIR, status, self.cancel_event),
                         self.auto_production_done, self.retry_issue_button,
                         title='重新制作异常章节', cancelable=True)

    def open_project(self, folder):
        try:
            self.current_chapter = 0
            self.previewed_segment = None
            self.previewed_card = None
            self.project_path = Path(folder)
            self.project = load_project(folder)
            flow = ensure_workflow(self.project)
            for key, widget in [('sample_size', self.sample_size), ('batch_size', self.batch_size), ('pilot_count', self.pilot_count)]:
                widget.blockSignals(True)
                widget.setValue(flow[key])
                widget.blockSignals(False)
            # Migrate metadata only; audio engines and existing renders are never silently changed.
            library = {v['id']: v for v in load_library(APP_DIR / 'voice-library')}
            for profile in [c['voice_profile'] for c in self.project['characters']] + [self.project.get('narrator_voice_profile', {})]:
                asset = library.get(profile.get('library_voice_id'), {})
                for key in ('reference_transcript', 'seed', 'design_prompt'):
                    if key in asset:
                        profile.setdefault(key, asset[key])
            sound_dir = self.project_path / "sounds"
            generated = generate_preview_library(sound_dir)
            for asset in generated:
                if not any(item.get("id") == asset["id"] for item in self.project["sound_cues"]):
                    self.project["sound_cues"].append({**asset, "enabled": False, "gain": 0.15, "position": 0.0})
            assets_by_id = {item["id"]: item for item in self.project["sound_cues"]}
            for chapter in self.project.get("chapters", []):
                chapter.setdefault('production_state', '待分析' if not chapter.get('segments') else '待生成')
                chapter.setdefault('production_issues', [])
                if repair_analyzed_segments(chapter.get('text', ''), chapter.get('segments', [])):
                    chapter['script_approval'] = None
                    chapter['audio_path'] = None
                if not chapter.get("cues"):
                    chapter["cues"] = recommend_cues(chapter["text"])
                    for cue in chapter["cues"]:
                        if cue["id"] in assets_by_id:
                            cue["asset"] = assets_by_id[cue["id"]]["path"]
            self.project.setdefault("narrator_voice_profile", {
                "source": None, "reference_audio": None, "reference_sha256": None,
                "engine": "breeze-tts-2", "model_revision": None,
                "settings": {"speed": 1.0, "emotion_strength": 0.35, "pause_scale": 1.0},
            })
            self.reverb_checkbox.blockSignals(True)
            self.reverb_checkbox.setChecked(bool(self.project.get("audio_settings", {}).get("reverb", False)))
            self.reverb_checkbox.blockSignals(False)
            self.loudness_combo.blockSignals(True)
            self.loudness_combo.setCurrentIndex(max(0, self.loudness_combo.findData(self.project.get('audio_settings', {}).get('lufs', -19))))
            self.loudness_combo.blockSignals(False)
            self.denoise_checkbox.blockSignals(True)
            self.denoise_checkbox.setChecked(bool(self.project.get('audio_settings', {}).get('denoise', False)))
            self.denoise_checkbox.blockSignals(False)
            save_project(folder, self.project)
            self.project_title.setText(self.project["title"])
            self.project_controls.show()
            self.empty_panel.hide()
            self.editor_tabs.setVisible(self.advanced_button.isChecked())
            self._refresh_chapter_list()
            self._load_recent()
            self._refresh_engine_status()
            self.refresh_workflow()
            self.steps.setCurrentIndex(self.recommended_step())
        except Exception as exc:
            QMessageBox.critical(self, "项目无法打开", str(exc))

    def _refresh_chapter_list(self):
        self.chapters_widget.blockSignals(True)
        self.chapters_widget.clear()
        for chapter in self.project.get("chapters", []):
            state = ' ✓已导出' if chapter.get('audio_path') else ' · 已校对' if is_reviewed(self.project, chapter) else ' · 待校对' if chapter.get('analysis_state') == 'analyzed' else ''
            self.chapters_widget.addItem(chapter["title"] + state)
        self.chapters_widget.blockSignals(False)
        if self.project["chapters"]:
            self.chapters_widget.setCurrentRow(min(self.current_chapter, len(self.project["chapters"]) - 1))
        self._refresh_cast()

    def select_chapter(self, row):
        if not self.project or row < 0 or row >= len(self.project["chapters"]):
            return
        self._save_current_chapter()
        self.current_chapter = row
        chapter = self.project["chapters"][row]
        self.chapter_heading.setText(chapter["title"])
        self._populate_segments()
        self._populate_cues()
        self.refresh_workflow()

    def _populate_segments(self):
        chapter = self.project["chapters"][self.current_chapter]
        segments = chapter.setdefault("segments", [])
        self._updating_table = True
        self.segments_table.setRowCount(len(segments))
        character_names = [item["name"] for item in self.project.get("characters", [])]
        for row, segment in enumerate(segments):
            text_item = QTableWidgetItem(segment.get("text", ""))
            self.segments_table.setItem(row, 0, text_item)
            kind = QComboBox()
            kind.addItems(["旁白", "对话"])
            kind.setCurrentIndex(1 if segment.get("kind") == "dialogue" else 0)
            kind.currentIndexChanged.connect(lambda value, r=row: self._set_segment(r, "kind", "dialogue" if value else "narration"))
            self.segments_table.setCellWidget(row, 1, kind)
            speaker = QComboBox()
            speaker.addItem("未确认", None)
            speaker.addItem("旁白", "__narrator__")
            for name in character_names:
                speaker.addItem(name, name)
            selected = '__narrator__' if segment.get('kind') == 'narration' else segment.get("speaker")
            speaker.setCurrentIndex(max(0, speaker.findData(selected)))
            speaker.currentIndexChanged.connect(lambda _v, combo=speaker, r=row: self._set_segment(r, "speaker", combo.currentData()))
            voice = profile_for(self.project, segment)
            speaker.setToolTip('已自动关联音色：' + voice.get('voice_name', '固定声卡') if voice.get('reference_audio') else '尚无声卡，请先在第 2 步自动配声；说话人不明确时需要校对。')
            self.segments_table.setCellWidget(row, 2, speaker)
            emotion = QComboBox()
            emotion_values = [("平静", "neutral"), ("开心", "happy"), ("悲伤", "sad"), ("愤怒", "angry"), ("害怕", "afraid"), ("惊讶", "surprised"), ("沉静", "calm")]
            for label, code in emotion_values:
                emotion.addItem(label, code)
            emotion.setCurrentIndex(max(0, emotion.findData(segment.get("emotion", "neutral"))))
            emotion.currentIndexChanged.connect(lambda _v, combo=emotion, r=row: self._set_segment(r, "emotion", combo.currentData()))
            self.segments_table.setCellWidget(row, 3, emotion)
            play = QPushButton("播放" if segment.get("audio_path") else "未生成")
            play.setEnabled(bool(segment.get("audio_path")))
            play.clicked.connect(lambda _=False, r=row: self.preview_segment(r))
            audition = QWidget()
            audition_layout = QVBoxLayout(audition)
            audition_layout.setContentsMargins(0, 0, 0, 0)
            audition_layout.setSpacing(2)
            audition_layout.addWidget(play)
            redo = QPushButton('重做')
            redo.setToolTip('重新生成这一句，保持人物声音不变。')
            redo.clicked.connect(lambda _=False, r=row: self.redo_segment(r))
            audition_layout.addWidget(redo)
            self.segments_table.setCellWidget(row, 4, audition)
            quality = segment.get("quality") or {}
            state = segment.get("review_state", "not_rendered")
            labels = {"passed": "通过", "approved": "已试听确认", "failed": "音色异常", "unverified": "需试听", "not_rendered": "待生成"}
            confirm = QPushButton(labels.get(state, "需确认"))
            confirm.setEnabled(state in {"failed", "unverified"} and bool(segment.get("audio_path")))
            confirm.clicked.connect(lambda _=False, r=row: self.confirm_voice(r))
            if quality.get("score") is not None:
                confirm.setToolTip(f"声纹相似度：{quality['score']:.2f}。{quality.get('message', '')}")
            else:
                confirm.setToolTip(quality.get("message", "试听该段并确认角色音色。"))
            self.segments_table.setCellWidget(row, 5, confirm)
            for col, key, low, high, step, default in [
                (6, 'emotion_strength', 0, .8, .05, .35),
                (7, 'pace', .85, 1.15, .05, 1.), (8, 'pause_after', .1, 1.5, .05, .25),
            ]:
                spin = QDoubleSpinBox()
                spin.setRange(low, high)
                spin.setSingleStep(step)
                spin.setValue(segment.get(key, default))
                spin.valueChanged.connect(lambda value, r=row, k=key: self._set_segment(r, k, value))
                self.segments_table.setCellWidget(row, col, spin)
            self.segments_table.setRowHeight(row, 80)
        self._updating_table = False
        self.segment_count.setText(f"{len(segments)} 段 · 人物与情绪可逐段修改")

    def _segment_text_changed(self, item):
        if not self._updating_table:
            self._set_segment(item.row(), "text", item.text())

    def preview_segment(self, row):
        if not self.project:
            return
        segment = self.project["chapters"][self.current_chapter]["segments"][row]
        path = self.project_path / segment.get("audio_path", "")
        if path.is_file():
            self.previewed_segment = (self.project["chapters"][self.current_chapter]["id"], row)
            self.audio_player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            self.audio_player.play()
            self.statusBar().showMessage(f"正在试听第 {row + 1} 段")

    def confirm_voice(self, row):
        if not self.project:
            return
        chapter = self.project["chapters"][self.current_chapter]
        segment = chapter["segments"][row]
        if (segment.get('quality') or {}).get('technical', {}).get('state') == 'failed':
            QMessageBox.warning(self, '音频基础检查失败', '静音、失真或时长异常不能直接放行，请重新生成。')
            return
        if self.previewed_segment != (chapter["id"], row):
            QMessageBox.information(self, "先试听", "请先播放这一段，再确认角色音色。")
            return
        segment["review_state"] = "approved"
        self._save_project()
        self._populate_segments()

    def _set_audio_setting(self, key, value):
        if self.project:
            self.project.setdefault("audio_settings", {})[key] = value
            for chapter in self.project['chapters']:
                chapter['audio_path'] = None
            self._save_project()

    def _set_segment(self, row, key, value):
        if not self.project:
            return
        chapter = self.project["chapters"][self.current_chapter]
        if row >= len(chapter.get("segments", [])):
            return
        chapter["audio_path"] = None
        chapter["segments"][row][key] = value
        self._invalidate_segment(chapter["segments"][row])
        self._save_project()
        self.refresh_workflow()

    def _invalidate_segment(self, segment):
        segment["review_state"] = "not_rendered"
        segment["render_signature"] = None
        segment["audio_path"] = None
        segment["quality"] = None
        segment["start"] = 0.0
        segment["duration"] = 0.0

    def _save_current_chapter(self):
        if self.project:
            self._save_project()

    def _save_project(self):
        if self.project and self.project_path:
            save_project(self.project_path, self.project)

    def _refresh_cast(self):
        self.cast_widgets = {'旁白': self.narrator_group}
        while self.cast_list.count():
            item = self.cast_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        profile = self.project.get("narrator_voice_profile", {})
        self.narrator_status.setText(('已自动关联：' + profile.get('voice_name', '固定音色') + (' · 已确认' if profile.get('sample_approved') else ' · 待试听')) if profile.get("reference_audio") else "点击第 2 步自动配声")
        for character in self.project.get("characters", []):
            group = QGroupBox(character["name"])
            self.cast_widgets[character['name']] = group
            group_layout = QVBoxLayout(group)
            tier = QComboBox(); tier.addItems(['A', 'B', 'C']); tier.setCurrentText(character.get('tier', 'C'))
            tier.currentTextChanged.connect(lambda value, c=character: self.set_tier(c, value))
            group_layout.addWidget(tier)
            stats = character.get('statistics', {})
            summary = QLabel(f"出场 {stats.get('chapter_count', 0)} 章 · 台词 {stats.get('dialogue_chars', 0)} 字\n主角相邻对白 {stats.get('interactions', 0)} 次 · 建议 {character.get('suggested_tier', 'C')} 级")
            summary.setWordWrap(True)
            group_layout.addWidget(summary)
            traits = QLabel(' / '.join(character.get(k, '未指定') for k in ('gender', 'age', 'timbre')) + '\n' + character.get('personality', ''))
            traits.setWordWrap(True)
            traits.setToolTip(character.get('description', ''))
            group_layout.addWidget(traits)
            edit = QPushButton('校对人物特征')
            edit.clicked.connect(lambda _=False, c=character: self.edit_character_traits(c))
            group_layout.addWidget(edit)
            profile = character['voice_profile']
            status = QLabel(('已自动关联台词：' + profile.get('voice_name', '自选音色') + (' · 已确认' if profile.get('sample_approved') else ' · 待试听')) if profile.get('reference_audio') else '点击第 2 步自动配声')
            status.setWordWrap(True)
            status.setObjectName("muted")
            group_layout.addWidget(status)
            bind = QPushButton("更换音色（可选）")
            bind.clicked.connect(lambda _=False, c=character: self.bind_voice(c))
            group_layout.addWidget(bind)
            for label, handler in [('试听声卡', self.preview_card), ('确认声音与文字稿', self.approve_card), ('重新设计声音', self.redesign_character)]:
                button = QPushButton(label)
                button.clicked.connect(lambda _=False, c=character, fn=handler: fn(c))
                group_layout.addWidget(button)
            self.cast_list.addWidget(group)

    def _set_gender(self, character, value):
        character["gender"] = value
        for chapter in self.project["chapters"]:
            chapter["audio_path"] = None
            for segment in chapter.get("segments", []):
                if segment.get("speaker") == character["name"]:
                    self._invalidate_segment(segment)
        self._save_project()

    def add_person(self):
        if not self.project:
            return
        name, ok = QInputDialog.getText(self, "添加人物", "人物名称")
        if not ok or not name.strip():
            return
        character = add_character(self.project, name.strip())
        self._save_project()
        self._refresh_cast()
        self._populate_segments()

    def bind_voice(self, character):
        if not self.project:
            return
        voices = load_library(APP_DIR / 'voice-library')
        options = [f"{i+1}. {v['name']} · {v.get('gender', '未指定')} · {v.get('age', '未指定')}" for i, v in enumerate(voices)] + ["临时从电脑导入 WAV…"]
        choice, ok = QInputDialog.getItem(self, "选择角色音色", "音色来源", options, 0, False)
        if not ok:
            return
        voice = voices[options.index(choice)] if options.index(choice) < len(voices) else None
        source = None
        if voice is None:
            source, _ = QFileDialog.getOpenFileName(self, "选择角色参考音频", str(Path.home()), "WAV 音频 (*.wav)")
        if not source and voice is None:
            return
        try:
            if character is None:
                character = {"id": "narrator", "voice_profile": self.project["narrator_voice_profile"]}
            if voice:
                bind_library_voice(self.project_path, character, voice, APP_DIR / 'voice-library')
            else:
                bind_reference_audio(self.project_path, character, source, source_kind='uploaded')
                character['voice_profile'].pop('library_voice_id', None)
                character['voice_profile']['voice_name'] = Path(source).stem
            if character["id"] == "narrator":
                self.project["narrator_voice_profile"] = character["voice_profile"]
            for chapter in self.project["chapters"]:
                chapter["audio_path"] = None
                for segment in chapter.get("segments", []):
                    is_narrator = segment.get('kind') == 'narration' or segment.get('speaker') == '__narrator__'
                    if (character['id'] == 'narrator' and is_narrator) or (character['id'] != 'narrator' and segment.get('speaker') == character['name']):
                        self._invalidate_segment(segment)
            self._save_project()
            self._refresh_cast()
            self._populate_segments()
            QMessageBox.information(self, "音色已绑定", "此人物后续片段使用同一固定声卡和 Breeze 模型。更换音色后，旧片段需要重新生成并试听。")
        except Exception as exc:
            QMessageBox.critical(self, "绑定失败", str(exc))

    def _get_analyzer(self):
        return APINovelAnalyzer(self.api_url.text(), self.api_model.text(), self.api_key.text())

    def save_api_settings(self):
        try:
            protected = protect_key(self.api_key.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "密钥保存失败", str(exc))
            return False
        self.settings["analysis_api_url"] = self.api_url.text().strip()
        self.settings["analysis_api_model"] = self.api_model.text().strip()
        self.settings["analysis_api_key_protected"] = protected
        try:
            _save_settings(self.settings)
        except OSError:
            QMessageBox.warning(self, "设置保存失败", "无法写入本机设置文件，请检查文件权限和磁盘空间。")
            return False
        self.statusBar().showMessage("接口设置已保存，密钥已加密，重启后自动读取。", 8000)
        return True

    def test_api_connection(self):
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            QMessageBox.warning(self, "请配置 NewAPI", str(exc))
            return
        if not self.save_api_settings():
            return
        self._run_worker(lambda status: analyzer.test_connection(),
                         lambda message: QMessageBox.information(self, "连接测试", message), title='测试 NewAPI 连接')

    def choose_tts_python(self):
        current = self.tts_python.text().strip()
        path, _ = QFileDialog.getOpenFileName(self, "选择 IndexTTS 环境的 Python", str(Path(current).parent if current else Path.home()), "Python (*.exe)")
        if path:
            self.tts_python.setText(path)
            self.save_tts_runtime()

    def choose_index_model_dir(self):
        current = self.index_model_path.text().strip()
        path = QFileDialog.getExistingDirectory(
            self, "选择已安装的 IndexTTS 2.5 模型目录",
            current if current and Path(current).is_dir() else str(Path.home()),
        )
        if path:
            self.index_model_path.setText(path)
            self.index_model_dir = Path(path)
            self.settings["index_model_dir"] = path
            _save_settings(self.settings)
            self._refresh_engine_status()

    def save_tts_runtime(self):
        python_path = Path(self.tts_python.text().strip()).expanduser()
        if not python_path.is_file():
            QMessageBox.warning(self, "Python 路径无效", "请选择现有 IndexTTS 环境中的 python.exe。程序不会安装或修改该环境。")
            return
        model_path = Path(self.index_model_path.text().strip()).expanduser()
        self.index_model_dir = model_path
        self.settings["indextts_python"] = str(python_path)
        self.settings["index_model_dir"] = str(model_path)
        _save_settings(self.settings)
        try:
            validate_index_model(model_path)
            status = f"环境和模型路径已保存。模型版本：{index_model_revision(model_path)[:12]}"
        except Exception as exc:
            status = f"路径已保存，但模型预检未通过：{exc}"
        self.download_status.setText(status)
        self._refresh_engine_status()

    def analyze_chapter(self):
        if not self.project:
            return
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            self.pages.setCurrentIndex(1)
            QMessageBox.warning(self, "请配置 NewAPI", str(exc))
            return
        if not self.save_api_settings():
            return
        self._active_analyzer = analyzer
        chapter = self.project["chapters"][self.current_chapter]
        known = self.project['characters']
        text = chapter["text"]
        self.analyze_button.setEnabled(False)
        def analyze(status):
            try:
                return analyzer.analyze_chapter(text, known, status, cancel=self.cancel_event)
            except AnalysisCancelled:
                return None, None
        self._run_worker(analyze, self._analysis_done, self.analyze_button, title='第 1 步 · 分析当前章节')

    def _analysis_done(self, result):
        segments, portraits = result
        if segments is None:
            self.project = load_project(self.project_path)
            self._refresh_chapter_list()
            self.refresh_workflow()
            return
        chapter = self.project["chapters"][self.current_chapter]
        chapter["segments"] = [vars(segment) for segment in segments]
        chapter["audio_path"] = None
        analyzer = getattr(self, '_active_analyzer', None)
        chapter["summary"] = getattr(analyzer, 'last_summary', '')
        chapter["mentioned_characters"] = getattr(analyzer, 'last_mentions', [])
        self.project.setdefault("engine", {})["analysis"] = "newapi"
        merge_characters(self.project, portraits)
        resolve_speakers(self.project)
        chapter['analysis_state'] = 'analyzed'
        update_statistics(self.project)
        matching = self.match_voices(show_result=False)
        self._save_project()
        self._refresh_cast()
        self._populate_segments()
        QMessageBox.information(self, '人物库已更新', '人物特征和逐句情绪已分析，原文未改写。\n' + (matching or '') + '\n请校对后生成配音。')

    def render_chapter(self):
        self.start_render([self.current_chapter])

    def render_pilot(self):
        if self.project:
            n = self.pilot_count.value()
            ensure_workflow(self.project)['pilot_count'] = n
            self.start_render(list(range(min(n, len(self.project['chapters'])))))

    def render_batch(self):
        if not self.project:
            return
        batches = ensure_workflow(self.project)['batches']
        if not batches:
            QMessageBox.information(self, '尚无批次', '请先分析首轮章节。')
            return
        batch = batches[-1]
        if batch['state'] == 'analyzing':
            QMessageBox.information(self, '分析未完成', '请先继续分析当前批次。')
            return
        self.start_render(list(range(batch['start'], batch['end'])))

    def redo_segment(self, row):
        self.start_render([self.current_chapter], segment_indices=[row], force=True)

    def start_render(self, indices, segment_indices=None, force=False):
        if not self.project:
            return
        flow = ensure_workflow(self.project)
        if any(i >= flow['pilot_count'] for i in indices) and not pilot_ready(self.project):
            QMessageBox.warning(self, '请先完成试播', '先生成前 1～2 章，逐段试听，再点击“确认试播通过”。')
            return
        if any(not is_reviewed(self.project, self.project['chapters'][i]) for i in indices):
            unresolved = next(i for i in indices if not is_reviewed(self.project, self.project['chapters'][i]))
            self.current_chapter = unresolved
            self.chapters_widget.setCurrentRow(unresolved)
            self.show_review_issues()
            return
        previous = self.project.get('engine', {}).get('tts')
        if previous != 'breeze-tts-2' and any(s.get('audio_path') for c in self.project['chapters'] for s in c.get('segments', [])):
            QMessageBox.warning(self, '旧项目使用其他配音引擎', '本项目已有其他引擎的音频。请重新导入小说建立 Breeze 项目，避免新旧声线混用。')
            return
        self.save_breeze_settings()
        config = {k: v.text().strip() for k, v in self.breeze_paths.items()}
        config['asr_model'] = self.asr_path.text().strip()
        config['segment_indices'] = segment_indices
        config['force'] = force
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        self._run_worker(lambda status: render_queue(snapshot, self.project_path, indices, config, status, self.cancel_event),
                         self.render_queue_done, title='第 4 步 · 生成配音', cancelable=True)

    def render_queue_done(self, result):
        self.previewed_segment = None
        apply_results(self.project, self.project_path, result)
        self._save_project()
        self._populate_segments()
        self._refresh_chapter_list()
        self.refresh_workflow()
        QMessageBox.information(self, '生成队列', result['message'] + '\n未自动通过的片段需要逐段试听确认，失败片段不会混入导出。')

    def refresh_workflow(self):
        if not self.project:
            for i in range(1, 5):
                self.steps.setTabEnabled(i, False)
            self.batch_analyze_button.setEnabled(False)
            return
        for i in range(5):
            self.steps.setTabEnabled(i, True)
        self.batch_analyze_button.setEnabled(True)
        flow = ensure_workflow(self.project)
        batches = flow['batches']
        batch = batches[-1] if batches else None
        text = ('尚未进行首轮采样分析。' if not batch else
                f"当前批次：第 {batch['start']+1}～{batch['end']} 章；已分析 {len(batch['completed'])} 章。")
        reviewed = sum(is_reviewed(self.project, c) for c in self.project['chapters'])
        step_names = ['导入与分析', '准备声音', '校对剧本', '试播与生成', '音效与导出']
        step = self.recommended_step()
        self.workflow_status.setText(f'建议下一步：{step + 1} {step_names[step]}。 ' + text + f' 已校对 {reviewed} 章。')
        self.sample_size.setEnabled(not batches)
        for index, chapter in enumerate(self.project['chapters']):
            item = self.chapters_widget.item(index)
            if item:
                state = ' ✓已导出' if chapter.get('audio_path') else ' · 已校对' if is_reviewed(self.project, chapter) else ' · 待校对'
                item.setText(chapter['title'] + state)

    def recommended_step(self):
        if not self.project:
            return 0
        batches = ensure_workflow(self.project)['batches']
        if not batches or batches[-1]['state'] == 'analyzing':
            return 0
        profiles = [self.project.get('narrator_voice_profile', {})] + [c['voice_profile'] for c in self.project['characters']]
        if any(not p.get('sample_approved') for p in profiles):
            return 1
        batch = batches[-1]
        chapters = self.project['chapters'][batch['start']:batch['end']]
        if not pilot_ready(self.project):
            pilot = self.project['chapters'][:ensure_workflow(self.project)['pilot_count']]
            return 2 if any(not is_reviewed(self.project, c) for c in pilot) else 3
        if any(not is_reviewed(self.project, c) for c in chapters):
            return 2
        return 4 if all(export_is_allowed({'chapters': [c]})[0] for c in chapters) else 3

    def _step_changed(self, index):
        if hasattr(self, 'editor_tabs'):
            self.editor_tabs.setCurrentIndex(1 if index == 4 else 0)

    def next_chapter(self):
        if self.project and self.current_chapter + 1 < len(self.project['chapters']):
            self.chapters_widget.setCurrentRow(self.current_chapter + 1)
        else:
            self.task_feedback.setText('已经是最后一章。校对完成后，请进入第 4 步生成。')
            self.task_feedback.show()

    def _set_workflow_option(self, key, value):
        if not self.project:
            return
        flow = ensure_workflow(self.project)
        flow[key] = value
        if key == 'sample_size' and flow['pilot_count'] > value:
            self.pilot_count.setValue(value)
        if key == 'pilot_count' and not flow['batches'] and value > flow['sample_size']:
            self.sample_size.setValue(value)
        self._save_project()
        self.refresh_workflow()

    def analyze_next_batch(self):
        if not self.project:
            return
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            QMessageBox.warning(self, '配置 NewAPI', str(exc))
            return
        if not self.save_api_settings():
            return
        self._active_analyzer = analyzer
        flow = ensure_workflow(self.project)
        if not flow['batches']:
            flow['sample_size'] = self.sample_size.value()
        flow['batch_size'] = self.batch_size.value()
        flow['pilot_count'] = self.pilot_count.value()
        self._save_project()
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        self._run_worker(lambda status: analyze_batch(snapshot, self.project_path, analyzer, status, self.cancel_event),
                         self.batch_analysis_done, title='第 1 步 · 批次人物与对白分析', cancelable=True)

    def batch_analysis_done(self, result):
        self.project, message = result
        for chapter in self.project.get('chapters', []):
            resolve_speakers({'characters': self.project['characters'], 'chapters': [chapter]})
        matching = self.match_voices(show_result=False)
        self._save_project()
        self._refresh_chapter_list()
        self.refresh_workflow()
        self.steps.setCurrentIndex(self.recommended_step())
        QMessageBox.information(self, '批次分析', message + '\n' + (matching or ''))

    def approve_script(self):
        if not self.project:
            return
        try:
            review_chapter(self.project, self.project['chapters'][self.current_chapter])
            self._save_project()
            self.refresh_workflow()
            self.task_feedback.setText('本章校对已通过。可选择下一章继续校对，或进入第 4 步生成试播。')
            self.task_feedback.show()
        except ValueError:
            self.show_review_issues()

    def show_review_issues(self):
        if not self.project:
            return
        chapter = self.project['chapters'][self.current_chapter]
        issues = chapter_issues(self.project, chapter)
        if not issues:
            QMessageBox.information(self, '本章已完成', '当前章节的人物、说话人、固定声音和文字稿都已确认。')
            return
        dialog = ReviewDialog(self, chapter['title'], issues)
        dialog.exec()
        if dialog.action == 'locate':
            issue = dialog.selected_issue()
            if issue.get('row') is not None:
                self.segments_table.scrollToItem(self.segments_table.item(issue['row'], 0))
                self.segments_table.selectRow(issue['row'])
            if issue.get('speaker') and issue['speaker'] in getattr(self, 'cast_widgets', {}):
                widget = self.cast_widgets[issue['speaker']]
                widget.setStyleSheet('QGroupBox { border: 2px solid #1b6c80; }')
                QTimer.singleShot(1800, lambda w=widget: w.setStyleSheet(''))
            self.task_feedback.setText('已定位到：' + issue['message'])
            self.task_feedback.show()
        elif dialog.action == 'ai':
            self.ai_complete_review()

    def ai_complete_review(self):
        if not self.project:
            return
        try:
            analyzer = self._get_analyzer()
        except ValueError as exc:
            self.pages.setCurrentIndex(1)
            QMessageBox.warning(self, '配置 NewAPI', str(exc))
            return
        if not self.save_api_settings():
            return
        self.save_breeze_settings()
        config = {k: v.text().strip() for k, v in self.breeze_paths.items()}
        config['asr_model'] = self.asr_path.text().strip()
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        index = self.current_chapter
        self._run_worker(lambda status: complete_chapter(snapshot, self.project_path, index, analyzer, config, APP_DIR, status, self.cancel_event),
                         self.ai_complete_done, title='AI 补全未确认项并自动配声', cancelable=True)

    def ai_complete_done(self, result):
        self.project = result['project']
        self._save_project(); self._refresh_cast(); self._populate_segments(); self.refresh_workflow()
        self.task_feedback.setText(result['message']); self.task_feedback.show()
        QMessageBox.information(self, 'AI 补全完成', result['message'] + '\n请试听自动绑定的声音并确认准确文字稿。')

    def confirm_pilot(self):
        if not self.project:
            return
        try:
            approve_pilot(self.project)
            self._save_project()
            self.refresh_workflow()
            self.task_feedback.setText('试播已通过。请校对其余章节，再点击“③ 生成当前批次 / 续做”。')
            self.task_feedback.show()
        except ValueError as exc:
            QMessageBox.warning(self, '试播未通过', str(exc))

    def select_protagonist(self):
        if not self.project or not self.project['characters']:
            return
        names = [c['name'] for c in self.project['characters']]
        name, ok = QInputDialog.getItem(self, '指定主角', '用于计算角色与主角的相邻对白次数（统计近似值）', names, 0, False)
        if ok:
            ensure_workflow(self.project)['protagonist_id'] = next(c['id'] for c in self.project['characters'] if c['name'] == name)
            update_statistics(self.project)
            self._save_project()
            self._refresh_cast()

    def set_tier(self, character, tier):
        character.update(tier=tier, tier_locked=True)
        self._save_project()

    def preview_card(self, character=None):
        profile = character['voice_profile'] if character else self.project.get('narrator_voice_profile', {})
        if profile.get('reference_audio'):
            self.audio_player.setSource(QUrl.fromLocalFile(str(self.project_path / profile['reference_audio'])))
            self.audio_player.play()
            self.previewed_card = character['id'] if character else 'narrator'

    def approve_card(self, character=None):
        cid = character['id'] if character else 'narrator'
        if getattr(self, 'previewed_card', None) != cid:
            QMessageBox.information(self, '先试听声卡', '请先试听此人物的固定声音，再确认录音准确文字稿。')
            return
        profile = character['voice_profile'] if character else self.project['narrator_voice_profile']
        text, ok = QInputDialog.getMultiLineText(self, '确认声音与文字稿', '请将文字修改为录音实际说出的完整内容；确认后锁定声卡。', profile.get('reference_transcript', ''))
        if ok and text.strip():
            if profile.get('reference_transcript') != text.strip():
                for chapter in self.project['chapters']:
                    chapter['audio_path'] = None
                    for segment in chapter['segments']:
                        matches = ((cid == 'narrator' and (segment.get('kind') == 'narration' or segment.get('speaker') == '__narrator__'))
                                   or (character and segment.get('speaker') == character['name']))
                        if matches:
                            self._invalidate_segment(segment)
            profile.update(reference_transcript=text.strip(), sample_approved=True)
            self._save_project()
            self._refresh_cast()
            self.refresh_workflow()

    def stop_task(self):
        self.cancel_event.set()
        if self.task_dialog and not self.task_dialog.stopping:
            self.task_dialog.request_stop()
        self.stop_button.setEnabled(False)
        self.statusBar().showMessage('停止请求已收到，正在中断可取消的网络请求。')

    def _run_worker(self, function, on_success, button=None, title='正在处理任务', cancelable=False):
        if self._worker and self._worker.isRunning():
            if self.task_dialog:
                self.task_dialog.raise_()
                self.task_dialog.activateWindow()
            return
        self.centralWidget().setEnabled(False)
        self.cancel_event.clear()
        self.stop_button.setEnabled(cancelable)
        if button:
            button.setEnabled(False)
        self.task_feedback.setText('进行中：' + title)
        self.task_feedback.show()
        self.task_dialog = TaskDialog(self, title, cancelable)
        self.task_dialog.stop_requested.connect(self.stop_task)
        self.task_dialog.show()
        worker = Worker(function)
        worker.status.connect(self._task_progress)
        worker.failed.connect(lambda error: self._worker_failed(error, button))
        worker.succeeded.connect(lambda result: self._worker_succeeded(result, on_success, button))
        self._worker = worker
        worker.start()

    def _task_progress(self, event):
        if self.task_dialog:
            self.task_dialog.update_progress(event)
        detail = event if isinstance(event, str) else event.get('detail', '')
        self.statusBar().showMessage(detail)

    def _end_task(self, button):
        if self.task_dialog:
            self.task_dialog.finish()
            self.task_dialog.deleteLater()
            self.task_dialog = None
        self.centralWidget().setEnabled(True)
        self.stop_button.setEnabled(False)
        if button:
            button.setEnabled(True)

    def _worker_failed(self, error, button):
        self._end_task(button)
        if self.project_path:
            self.project = load_project(self.project_path)
            self._refresh_chapter_list()
            self.refresh_workflow()
        self.task_feedback.setText('任务未完成：' + str(error))
        self.task_feedback.show()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, '任务失败', error)

    def _worker_succeeded(self, result, callback, button):
        self._end_task(button)
        message = ('已停止，已完成内容已保留。' if self.cancel_event.is_set() else '任务处理结束，请查看结果并继续下一步。')
        if isinstance(result, dict):
            message = result.get('error') or result.get('message') or message
        elif isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            message = result[1]
        self.task_feedback.setText(message)
        self.task_feedback.show()
        try:
            callback(result)
            self.refresh_workflow()
        except Exception as exc:
            self._worker_failed(str(exc), button)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "任务正在运行", "请等待当前任务结束再关闭工作台。")
            event.ignore()
        else:
            event.accept()

    def refresh_cues(self):
        if not self.project:
            return
        chapter = self.project["chapters"][self.current_chapter]
        cues = recommend_cues(chapter["text"])
        for cue in cues:
            asset = next((a for a in self.project["sound_cues"] if a["id"] == cue["id"]), None)
            if asset:
                cue["asset"] = asset["path"]
        chapter["cues"] = cues
        self._save_project()
        self._populate_cues()

    def _populate_cues(self):
        if not self.project or not self.project.get("chapters"):
            return
        cues = self.project["chapters"][self.current_chapter].get("cues", [])
        self.cues_table.setRowCount(len(cues))
        for row, cue in enumerate(cues):
            checkbox = QCheckBox()
            checkbox.setChecked(cue.get("enabled", True))
            checkbox.stateChanged.connect(lambda state, r=row: self._set_cue(r, "enabled", bool(state)))
            self.cues_table.setCellWidget(row, 0, checkbox)
            name = QTableWidgetItem(cue["name"])
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.cues_table.setItem(row, 1, name)
            kind = "环境氛围" if cue["kind"] == "ambience" else "动作音效"
            self.cues_table.setItem(row, 2, QTableWidgetItem(kind))
            position = QDoubleSpinBox()
            position.setRange(0, 100)
            position.setSuffix(" %")
            position.setValue(cue.get("position", 0) * 100)
            position.valueChanged.connect(lambda value, r=row: self._set_cue(r, "position", value / 100))
            self.cues_table.setCellWidget(row, 3, position)
            volume = QDoubleSpinBox()
            volume.setRange(0, 1)
            volume.setSingleStep(0.05)
            volume.setValue(cue.get("gain", 0.15))
            volume.valueChanged.connect(lambda value, r=row: self._set_cue(r, "gain", value))
            self.cues_table.setCellWidget(row, 4, volume)
            asset = QComboBox()
            asset.addItem("无素材", None)
            for source in self.project.get("sound_cues", []):
                asset.addItem(source.get("name", source["id"]), source.get("path"))
            asset.setCurrentIndex(max(0, asset.findData(cue.get("asset"))))
            asset.currentIndexChanged.connect(lambda _v, combo=asset, r=row: self._set_cue(r, "asset", combo.currentData()))
            self.cues_table.setCellWidget(row, 5, asset)

    def _set_cue(self, row, key, value):
        if self.project and row < len(self.project["chapters"][self.current_chapter].get("cues", [])):
            self.project["chapters"][self.current_chapter]["cues"][row][key] = value
            self.project["chapters"][self.current_chapter]["audio_path"] = None
            self._save_project()

    def import_sound_asset(self):
        if not self.project:
            return
        source, _ = QFileDialog.getOpenFileName(self, "导入配乐或音效", str(Path.home()), "WAV 音频 (*.wav)")
        if not source:
            return
        kind, ok = QInputDialog.getItem(self, "素材类型", "选择用途", ["配乐 / 环境氛围", "动作音效"], 0, False)
        if not ok:
            return
        target = self.project_path / "sounds" / Path(source).name
        if Path(source).resolve() != target.resolve():
            import shutil
            shutil.copy2(source, target)
        item = {"id": str(uuid4()), "name": target.stem, "kind": "ambience" if kind.startswith("配乐") else "effect", "path": str(target), "license": "用户导入"}
        self.project["sound_cues"].append(item)
        self._save_project()
        QMessageBox.information(self, "素材已导入", "音频素材已复制到项目目录。请确认你拥有使用该素材的权利。")

    def export_chapter(self):
        if not self.project:
            return
        chapter = self.project["chapters"][self.current_chapter]
        if not is_reviewed(self.project, chapter):
            QMessageBox.warning(self, '剧本已修改或未确认', '请重新校对并生成后导出。')
            return
        try:
            if collect_jobs(self.project, self.project_path, [self.current_chapter], self.project.get('breeze_model_fingerprint', '')):
                raise ValueError('部分片段的文本、声卡或参数已变化，请重新生成本章后导出。')
        except Exception as exc:
            QMessageBox.warning(self, '配音需要更新', str(exc))
            return
        missing = [i + 1 for i, segment in enumerate(chapter.get("segments", [])) if not segment.get("audio_path")]
        if missing:
            QMessageBox.warning(self, "章节尚未生成完整", f"还有 {len(missing)} 段没有可用配音。请先完成本章生成，再导出。")
            return
        allowed, pending = export_is_allowed({"chapters": [chapter]})
        if not allowed:
            QMessageBox.warning(self, '还有音色需要确认', f'有 {len(pending)} 段未通过声纹检查或尚未试听。请逐段播放并确认后再导出。')
            return
        snapshot = json.loads(json.dumps(self.project, ensure_ascii=False))
        chapter_index = self.current_chapter
        def done(result):
            path = result['path']
            chapter = self.project['chapters'][chapter_index]
            chapter['audio_path'] = str(path.relative_to(self.project_path))
            chapter['master_sha256'] = file_hash(path)
            for segment, timing in zip(chapter['segments'], result['timings']):
                segment.update(timing)
            self._save_project()
            self._refresh_chapter_list()
            QMessageBox.information(self, '章节导出完成', f'已导出响度归一后的 WAV 和 MP3：\n{path.parent}')
        self._run_worker(lambda status: export_chapter_audio(snapshot, self.project_path, chapter_index, status), done, title='第 5 步 · 导出章节音频')


    def export_book(self):
        if not self.project:
            return
        count, ok = QInputDialog.getInt(self, '导出有声书', '从第 1 章开始，包含多少章？这些章节必须先完成章节导出。',
                                       len(self.project['chapters']), 1, len(self.project['chapters']))
        if not ok:
            return
        chapters = self.project['chapters'][:count]
        if any(not c.get('audio_path') or not is_reviewed(self.project, c) or not export_is_allowed({'chapters': [c]})[0]
               for c in chapters):
            QMessageBox.warning(self, '章节未就绪', '请先逐章完成校对、试听和 WAV / MP3 导出。')
            return
        try:
            if collect_jobs(self.project, self.project_path, list(range(count)), self.project.get('breeze_model_fingerprint', '')):
                raise ValueError('部分配音已变化或文件损坏，请重新生成并导出相关章节。')
            for chapter in chapters:
                master = self.project_path / chapter['audio_path']
                if not master.is_file() or chapter.get('master_sha256') != file_hash(master):
                    raise ValueError('章节母带缺失或已修改，请重新导出该章节。')
        except Exception as exc:
            QMessageBox.warning(self, '章节需要更新', str(exc))
            return
        target = self.project_path / 'exports' / f'book-{count}-{uuid4().hex[:8]}.m4b'
        ordered = [(c['title'], self.project_path / c['audio_path']) for c in chapters]
        title = self.project['title']
        self._run_worker(lambda status: export_m4b(ordered, target, title),
                         lambda _: QMessageBox.information(self, 'M4B 已导出', str(target)), title='第 5 步 · 导出分章有声书')


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = StudioWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
