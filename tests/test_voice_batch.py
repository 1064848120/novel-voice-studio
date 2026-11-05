import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from novel_studio.audio import write_wav
from novel_studio.project import add_character, load_project, save_project
from novel_studio.voice_batch import design_batch
from novel_studio.casting import resolve_speakers
from novel_studio.breeze_render import collect_jobs, file_hash


class VoiceBatchTests(unittest.TestCase):
    def test_each_voice_saved_and_bound_before_next_design_failure(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); folder = root / 'project'
            p = {'schema_version': 1, 'characters': [], 'chapters': []}
            one = add_character(p, '小明', '男声'); two = add_character(p, '小红', '女声')
            p['chapters'] = [{'id': 'ch1', 'audio_path': 'master.wav', 'segments': [
                {'text': '你好', 'kind': 'dialogue', 'speaker': '小明', 'audio_path': 'old1.wav'},
                {'text': '再见', 'kind': 'dialogue', 'speaker': '小红', 'audio_path': 'old2.wav'}]}]
            save_project(folder, p)
            sample = root / 'new.wav'; write_wav(sample, 24000, [.1] * 48000)
            calls = []
            def design(*args):
                calls.append(True)
                if len(calls) == 2:
                    saved = load_project(folder)
                    self.assertTrue(saved['characters'][0]['voice_profile']['reference_audio'])
                    self.assertIsNone(saved['chapters'][0]['segments'][0]['audio_path'])
                    self.assertEqual(saved['chapters'][0]['segments'][1]['audio_path'], 'old2.wav')
                    raise RuntimeError('第二个声音失败')
                return str(sample), {'reference_transcript': '你好', 'seed': 123}
            jobs = [(c['id'], {'name': c['name']}) for c in (one, two)]
            events = []
            config = dict(breeze_python='', breeze_runtime='', breeze_models='')
            with patch('novel_studio.voice_batch.design_voice', side_effect=design):
                result = design_batch(p, folder, root, jobs, config, events.append, threading.Event())
            self.assertEqual(result['completed'], 1)
            self.assertEqual(result['total'], 2)
            self.assertIn('第二个声音失败', result['error'])
            self.assertTrue(any(e['done'] == 1 and e['total'] == 2 for e in events))
            self.assertIsNone(two['voice_profile']['reference_audio'])

    def test_known_aliases_resolve_without_guessing_unknown_speakers(self):
        p = {'characters': [{'name': '李明', 'aliases': ['老李']}, {'name': '李红', 'aliases': []}],
             'chapters': [{'segments': [{'text': '你好', 'kind': 'dialogue', 'speaker': '老李'},
                                       {'text': '谁', 'kind': 'dialogue', 'speaker': None},
                                       {'text': '天黑了', 'kind': 'narration', 'speaker': None}]}]}
        resolve_speakers(p)
        self.assertEqual([s['speaker'] for s in p['chapters'][0]['segments']], ['李明', None, '__narrator__'])

    def test_new_take_uses_new_file_but_same_voice_and_seed(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); ref = root / 'reference.wav'
            write_wav(ref, 24000, [.1] * 48000)
            p = {'characters': [], 'narrator_voice_profile': {'reference_audio': str(ref),
                 'reference_sha256': file_hash(ref), 'reference_transcript': '你好', 'seed': 123},
                 'chapters': [{'id': 'ch1', 'segments': [{'kind': 'narration', 'text': '你好'},
                                                       {'kind': 'narration', 'text': '再见'}]}]}
            before = collect_jobs(p, root, [0], 'model', [0])[0]
            p['chapters'][0]['segments'][0]['render_take'] = 'redo1'
            after = collect_jobs(p, root, [0], 'model', [0])
            self.assertEqual(len(after), 1)
            self.assertNotEqual(before['output_path'], after[0]['output_path'])
            self.assertEqual(before['reference_sha256'], after[0]['reference_sha256'])
            self.assertEqual(before['seed'], after[0]['seed'])
