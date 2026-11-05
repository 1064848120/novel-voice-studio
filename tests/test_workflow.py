import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from novel_studio.workflow import (ensure_workflow, update_statistics, next_batch, analyze_batch,
    review_chapter, is_reviewed, approve_pilot, pilot_ready)
from novel_studio.importers import Segment
from novel_studio.casting import merge_characters
from novel_studio.project import load_project
from novel_studio.breeze_render import technical_check, instruction
from novel_studio.audio import write_wav
from unittest.mock import patch
from types import SimpleNamespace
from novel_studio.quality import compare_transcript
from novel_studio.breeze_render import collect_jobs, apply_results, file_hash
from novel_studio.project import export_is_allowed
from novel_studio.api_analysis import AnalysisCancelled


def project(n=35):
    return {'schema_version': 1, 'characters': [], 'chapters': [
        {'id': f'ch-{i}', 'title': f'第{i+1}章', 'text': '你好。', 'segments': [vars(Segment('你好。'))]}
        for i in range(n)]}


def bind(p):
    p['narrator_voice_profile'] = {'reference_audio': 'ref.wav', 'reference_transcript': '你好。', 'sample_approved': True}


class WorkflowTests(unittest.TestCase):
    def test_sampling_incremental_and_no_skip(self):
        p = project(45)
        first = next_batch(p)
        self.assertEqual((first['start'], first['end']), (0, 30))
        self.assertEqual(next_batch(p)['id'], first['id'])
        first.update(state='review', completed=list(range(30)))
        with self.assertRaisesRegex(ValueError, '试播'):
            next_batch(p)
        bind(p)
        for c in p['chapters'][:2]:
            review_chapter(p, c)
            c['segments'][0].update(audio_path='test.wav', review_state='approved')
        approve_pilot(p)
        second = next_batch(p)
        self.assertEqual((second['start'], second['end']), (30, 40))

    def test_cancel_resume_and_analysis_failure_persist_previous(self):
        p = project(3)
        cancel = threading.Event()
        class Fake:
            count = 0
            last_summary = '章节摘要'
            last_mentions = []
            def analyze_chapter(self, text, known, status, **kwargs):
                self.count += 1
                if self.count == 2:
                    raise RuntimeError('模拟网络中断')
                return [Segment(text)], []
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(RuntimeError, '网络中断'):
                analyze_batch(p, t, Fake(), lambda _: None, cancel)
            reopened = load_project(t)
            self.assertEqual(reopened['workflow']['batches'][0]['completed'], [0])
            cancel.set()
            result, _ = analyze_batch(reopened, t, Fake(), lambda _: None, cancel)
            self.assertEqual(result['workflow']['batches'][0]['completed'], [0])

    def test_cancelling_inflight_analysis_returns_completed_chapters(self):
        p = project(3)
        cancel = threading.Event()
        class Fake:
            last_summary = ''
            last_mentions = []
            def analyze_chapter(self, text, known, status, **kwargs):
                raise AnalysisCancelled()
        with tempfile.TemporaryDirectory() as t:
            result, message = analyze_batch(p, t, Fake(), lambda _: None, cancel)
            self.assertIn('已停止分析', message)
            self.assertEqual(result['workflow']['batches'][0]['completed'], [])
            self.assertEqual(load_project(t)['workflow']['batches'][0]['state'], 'analyzing')

    def test_review_and_pilot_invalidate_on_changes(self):
        p = project(2); bind(p)
        for c in p['chapters']:
            review_chapter(p,c)
            c['segments'][0].update(audio_path='a.wav',review_state='approved')
        approve_pilot(p)
        self.assertTrue(pilot_ready(p))
        p['chapters'][0]['segments'][0]['emotion']='sad'
        self.assertFalse(is_reviewed(p,p['chapters'][0]))
        self.assertFalse(pilot_ready(p))

    def test_promotion_does_not_change_identity_and_stats_not_double_counted(self):
        p = project(5)
        merge_characters(p,[{'name':'小明','gender':'男声'}])
        c = p['characters'][0]
        c['voice_profile']['reference_audio']='fixed.wav'
        voice = copy.deepcopy(c['voice_profile'])
        for ch in p['chapters']:
            ch['segments']=[vars(Segment('这是角色台词。','dialogue','小明'))]
        update_statistics(p); update_statistics(p)
        self.assertEqual(c['tier'],'A')
        self.assertEqual(c['statistics']['chapter_count'],5)
        self.assertEqual(c['voice_profile'],voice)

    def test_technical_failure_and_expression_retry(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'silence.wav'
            write_wav(path,24000,[0.]*24000)
            self.assertEqual(technical_check(path,'测试')['state'],'failed')
            write_wav(path,24000,[1.]*24000)
            self.assertEqual(technical_check(path,'测试')['state'],'failed')
        self.assertNotEqual(instruction({'emotion':'angry','emotion_strength':.4}),
                            instruction({'emotion':'angry','emotion_strength':.4},retry=True))

    def test_traditional_transcript_equivalent_but_missing_dialogue_fails(self):
        with tempfile.TemporaryDirectory() as t:
            model = SimpleNamespace(transcribe=lambda *a, **kw: ([SimpleNamespace(text='你好，我們終於又見面了！')], None))
            with patch('novel_studio.quality.asr_model', return_value=model):
                self.assertEqual(compare_transcript('dummy.wav', '你好，我们终于又见面了。', t)['state'], 'passed')
                self.assertEqual(compare_transcript('dummy.wav', '他拿起钥匙离开了房间，一句话也没有说。', t)['state'], 'failed')

    def test_modified_audio_cannot_reuse_approval(self):
        p = project(1); bind(p)
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            reference = root / 'ref.wav'
            write_wav(reference, 24000, [.1] * 48000)
            p['narrator_voice_profile']['reference_sha256'] = file_hash(reference)
            jobs = collect_jobs(p, root, [0], 'fixed-model')
            output = Path(jobs[0]['output_path'])
            write_wav(output, 24000, [.1] * 48000)
            row = dict(jobs[0], audio_path=str(output), audio_sha256=file_hash(output),
                       quality={'technical': {'state': 'passed'}}, review_state='passed')
            apply_results(p, root, {'fingerprint': 'fixed-model', 'rows': [row]})
            self.assertEqual(collect_jobs(p, root, [0], 'fixed-model'), [])
            output.write_bytes(b'corrupted')
            self.assertEqual(len(collect_jobs(p, root, [0], 'fixed-model')), 1)
            reference.write_bytes(b'changed reference')
            with self.assertRaisesRegex(ValueError, '参考录音'):
                collect_jobs(p, root, [0], 'fixed-model')

    def test_missing_or_technically_failed_audio_blocks_export(self):
        p = project(1)
        self.assertFalse(export_is_allowed(p)[0])
        p['chapters'][0]['segments'][0].update(audio_path='test.wav', review_state='approved',
                                             quality={'technical': {'state': 'failed'}})
        self.assertFalse(export_is_allowed(p)[0])
