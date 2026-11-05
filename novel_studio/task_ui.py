"""Visible, honest task feedback: completed work, activity and elapsed time."""
from time import monotonic
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QPlainTextEdit, QListWidget, QListWidgetItem


class ReviewDialog(QDialog):
    def __init__(self, parent, chapter_title, issues):
        super().__init__(parent)
        self.setWindowTitle('校对尚未完成 · ' + chapter_title)
        self.resize(680, 400)
        self.action = None
        self.issues = issues
        layout = QVBoxLayout(self)
        label = QLabel(f'发现 {len(issues)} 处待处理。选择一项可直接跳到台词或固定声音。')
        label.setWordWrap(True); layout.addWidget(label)
        self.items = QListWidget()
        for issue in issues:
            item = QListWidgetItem(issue['message']); item.setToolTip(issue['message'])
            self.items.addItem(item)
        self.items.setCurrentRow(0)
        self.items.itemDoubleClicked.connect(lambda _: self.choose('locate'))
        layout.addWidget(self.items)
        hint = QLabel('AI 可补全未确认说话人、匹配或设计缺少的声音；已确认的人物与声音保持不变。\n试听与录音文字稿核对仍由你完成。')
        hint.setWordWrap(True); layout.addWidget(hint)
        row = QHBoxLayout()
        for title, action in [('定位所选问题', 'locate'), ('AI 补全本章＋自动配声', 'ai'), ('关闭', 'close')]:
            button = QPushButton(title); button.clicked.connect(lambda _=False, a=action: self.choose(a)); row.addWidget(button)
        layout.addLayout(row)

    def choose(self, action):
        self.action = action
        self.accept()

    def selected_issue(self):
        return self.issues[max(0, self.items.currentRow())]


class TaskDialog(QDialog):
    stop_requested = Signal()

    def __init__(self, parent, title, cancelable=True):
        super().__init__(parent)
        self.setWindowTitle('任务进度 · ' + title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)
        self.resize(600, 320)
        self.running = True
        self.stopping = False
        self.started = monotonic()
        self.last_update = self.started
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.heading = QLabel(title)
        self.heading.setObjectName('pageTitle')
        layout.addWidget(self.heading)
        self.context = QLabel('任务已开始，请等待。无需重复点击。')
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        self.detail = QLabel('正在准备…')
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setMinimumHeight(20)
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.count = QLabel('正在准备，暂时无法计算百分比')
        layout.addWidget(self.count)
        self.subprogress = QLabel()
        self.subprogress.setObjectName('muted')
        self.subprogress.hide()
        layout.addWidget(self.subprogress)
        self.clock = QLabel()
        self.clock.setObjectName('muted')
        layout.addWidget(self.clock)
        self.is_analysis = '分析' in title or '识别' in title
        self.hint = QLabel(('分析请求未返回时会立即取消；已完成章节保留。' if self.is_analysis else
                            '停止后会保留已完成结果，当前章节或片段结束后停止。') if cancelable
                           else '请等待当前任务完成。模型加载、接口等待和导出耗时会因任务而异。')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        details = QPushButton('查看运行记录')
        details.setCheckable(True)
        row.addWidget(details)
        row.addStretch()
        self.stop = QPushButton('立即停止（保留成果）' if self.is_analysis else '停止并保留成果')
        self.stop.setVisible(cancelable)
        self.stop.clicked.connect(self.request_stop)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMaximumHeight(160)
        self.log.hide()
        details.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()

    def update_progress(self, event):
        event = {'detail': event} if isinstance(event, str) else event
        self.last_update = monotonic()
        detail = event.get('detail', '')
        if detail:
            self.detail.setText(detail)
            self.log.appendPlainText(detail)
        if event.get('context'):
            self.context.setText(event['context'])
        if 'total' in event:
            total, done = int(event['total']), int(event.get('done', 0))
            if total > 0:
                sub = event.get('subprogress')
                if sub and int(sub.get('total', 0)) > 0:
                    sub_total, sub_done = int(sub['total']), int(sub.get('done', 0))
                    self.bar.setRange(0, sub_total)
                    self.bar.setValue(max(0, min(sub_done, sub_total)))
                    self.count.setText(f"本批已完成 {done} / {total} {event.get('unit', '项')}；当前章节正在分析")
                    if sub.get('phase') == 'done':
                        self.subprogress.setText(f"当前章节已完成 {sub_done} / {sub_total} {sub.get('unit', '组')} · {sub_done * 100 // sub_total}%")
                    else:
                        self.subprogress.setText(f"正在等待当前章节第 {min(sub_done + 1, sub_total)} / {sub_total} {sub.get('unit', '组')}的接口响应 · 已完成 {sub_done} 组")
                    self.subprogress.show()
                else:
                    self.bar.setRange(0, total)
                    self.bar.setValue(max(0, min(done, total)))
                    self.count.setText(f"{event.get('label', '已处理')} {done} / {total} {event.get('unit', '项')} · {done * 100 // total}%" +
                                       '（检查结果见完成提示）')
                    self.subprogress.clear()
                    self.subprogress.hide()
            else:
                self.bar.setRange(0, 0)
        self.tick()

    def tick(self):
        elapsed = int(monotonic() - self.started)
        quiet = int(monotonic() - self.last_update)
        self.clock.setText(f'已用时 {elapsed // 60:02d}:{elapsed % 60:02d}' +
                           (f' · 当前操作已等待 {quiet} 秒，仍在运行' if quiet >= 15 else ' · 正在运行'))

    def request_stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.stop.setEnabled(False)
        self.stop.setText('正在立即停止…' if self.is_analysis else '正在等待安全停止…')
        self.hint.setText(('停止请求已收到。未返回的 NewAPI 请求将作废，已完成章节保留。' if self.is_analysis else
                           '停止请求已收到。正在中断可取消的网络请求；无法中断的单段生成会在当前片段结束后停止。'))
        self.stop_requested.emit()

    def finish(self):
        self.running = False
        self.timer.stop()
        self.accept()

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
        else:
            event.accept()
