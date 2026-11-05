import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from novel_studio.auto_production import run_auto_production
from novel_studio.importers import Segment


class AutoProductionTests(unittest.TestCase):
    def project(self):
        return {
            'title': '测试书', 'characters': [], 'chapters': [
                {'id': 'ch-1', 'title': '第一章', 'text': '正文。', 'segments': [], 'cues': []}
            ],
            'narrator_voice_profile': {'reference_audio': 'voices/narrator.wav',
                'reference_transcript': '你好。', 'sample_approved': True},
        }

    def test_one_click_pipeline_saves_completion_and_skips_next_run(self):
        project = self.project()
        class Analyzer:
            last_summary = '摘要'
            last_mentions = []
            def analyze_chapter(self, text, known, progress, **kwargs):
                return [Segment('正文。', kind='narration', speaker='__narrator__', speaker_confidence=1.0,
                                speaker_resolution='narration')], []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'voices').mkdir()
            (root / 'voices/narrator.wav').write_bytes(b'voice')
            config = {'breeze_python': 'python', 'breeze_runtime': 'runtime', 'breeze_models': 'models', 'asr_model': ''}
            def fake_render(snapshot, folder, indices, config, status, cancel):
                return {'fingerprint': 'fixed', 'rows': [{'chapter_index': 0, 'segment_index': 0,
                    'signature': 'sig', 'audio_path': str(root / 'audio.wav'), 'review_state': 'passed',
                    'quality': {'technical': {'state': 'passed'}, 'voice': {'state': 'passed'},
                                'transcript': {'state': 'passed'}}, 'audio_sha256': 'hash'}], 'message': 'ok'}
            (root / 'master.wav').write_bytes(b'master')
            with patch('novel_studio.breeze_render.render_queue', fake_render), \
                 patch('novel_studio.auto_production.export_chapter_audio', return_value={'path': root / 'master.wav'}), \
                 patch('novel_studio.auto_production.save_project'), \
                 patch('novel_studio.auto_production.file_hash', return_value='hash', create=True):
                result = run_auto_production(project, root, 0, 1, Analyzer(), config, root,
                                             lambda _: None, threading.Event())
            self.assertEqual(result['completed'], [0], result)
            self.assertEqual(project['chapters'][0]['production_state'], '已完成')


if __name__ == '__main__':
    unittest.main()
