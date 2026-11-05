import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
import novel_studio.app as ui
from novel_studio.task_ui import TaskDialog
from novel_studio.project import create_project


class TaskFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.appdir = patch.object(ui, 'APP_DIR', self.root / 'settings')
        self.projects = patch.object(ui, 'PROJECTS_DIR', self.root / 'projects')
        self.appdir.start(); self.projects.start()
        self.window = ui.StudioWindow()

    def tearDown(self):
        self.window.close()
        self.appdir.stop(); self.projects.stop()
        self.temp.cleanup()

    def pump_until(self, predicate):
        for _ in range(200):
            if predicate():
                return
            QTest.qWait(10)
        self.fail('任务反馈超时')

    def test_unknown_duration_counts_and_stop_acknowledged(self):
        dialog = TaskDialog(self.window, '测试任务')
        dialog.show()
        self.assertEqual(dialog.bar.maximum(), 0)
        dialog.update_progress({'context': '第 8 章', 'detail': '等待 NewAPI', 'done': 7, 'total': 30, 'unit': '章'})
        self.assertEqual(dialog.bar.value(), 7)
        self.assertIn('7 / 30', dialog.count.text())
        dialog.update_progress({'context': '第 8 章', 'detail': '等待接口响应', 'done': 7, 'total': 30,
                                'unit': '章', 'subprogress': {'done': 0, 'total': 2, 'unit': '组', 'phase': 'running'}})
        self.assertEqual(dialog.bar.maximum(), 2)
        self.assertIn('第 1 / 2 组', dialog.subprogress.text())
        dialog.update_progress({'detail': '第一组完成', 'done': 1, 'total': 30, 'unit': '章',
                                'subprogress': {'done': 1, 'total': 2, 'unit': '组', 'phase': 'done'}})
        self.assertEqual(dialog.bar.value(), 1)
        self.assertIn('已完成 1 / 2', dialog.subprogress.text())
        dialog.update_progress('正在等待模型加载')
        self.assertEqual(dialog.bar.value(), 1)  # Activity text cannot invent progress.
        stops = []
        dialog.stop_requested.connect(lambda: stops.append(True))
        dialog.request_stop(); dialog.request_stop()
        self.assertEqual(stops, [True])
        self.assertFalse(dialog.stop.isEnabled())
        dialog.reject()
        self.assertTrue(dialog.isVisible())  # Esc cannot silently dismiss running work.
        dialog.finish()
        self.assertFalse(dialog.isVisible())

    def test_worker_progress_cancel_and_success_restores_ui(self):
        entered = threading.Event()
        results = []
        def work(status):
            status({'detail': '处理第 1 章', 'done': 0, 'total': 2, 'unit': '章'})
            entered.set()
            self.window.cancel_event.wait(2)
            return 'preserved'
        self.window._run_worker(work, results.append, title='批次分析', cancelable=True)
        self.assertTrue(self.window.task_dialog.isVisible())
        self.assertFalse(self.window.centralWidget().isEnabled())
        self.pump_until(lambda: entered.is_set() and self.window.task_dialog.bar.maximum() == 2)
        self.window.task_dialog.request_stop()
        self.pump_until(lambda: bool(results))
        self.window._worker.wait(2000)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertIsNone(self.window.task_dialog)
        self.assertIn('已停止', self.window.task_feedback.text())

    def test_failure_is_visible_and_steps_are_numbered(self):
        self.assertEqual(self.window.steps.count(), 5)
        self.assertTrue(all(self.window.steps.tabText(i).startswith(str(i+1)) for i in range(5)))
        self.assertFalse(self.window.steps.isTabEnabled(1))
        source = self.root / 'book.txt'
        source.write_text('第一章 开始\n这是正文。', encoding='utf-8')
        self.window.open_project(create_project(source, ui.PROJECTS_DIR))
        self.assertTrue(self.window.steps.isTabEnabled(1))
        def fail(status):
            raise ValueError('模拟接口失败')
        with patch.object(QMessageBox, 'critical') as message:
            self.window._run_worker(fail, lambda _: None, title='分析')
            self.pump_until(lambda: message.called)
        self.window._worker.wait(2000)
        self.assertIn('模拟接口失败', self.window.task_feedback.text())
        self.assertTrue(self.window.centralWidget().isEnabled())
