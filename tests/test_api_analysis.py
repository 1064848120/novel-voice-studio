import io
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from novel_studio.api_analysis import APINovelAnalyzer, AnalysisCancelled
from novel_studio.importers import segment_text


class APIAnalysisTests(unittest.TestCase):
    def analyzer(self, url='https://example.invalid/v1'):
        return APINovelAnalyzer(url, 'test-model', 'test-secret')

    def test_endpoint_and_headers(self):
        for url in ('https://example.invalid', 'https://example.invalid/v1/',
                    'https://example.invalid/v1/chat/completions'):
            with patch('novel_studio.api_analysis.urlopen') as call:
                call.return_value = io.BytesIO(json.dumps({'choices': [{'message': {'content': '连接成功'}}]}).encode())
                self.analyzer(url).test_connection()
                request = call.call_args.args[0]
                self.assertEqual(request.full_url, 'https://example.invalid/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')
                self.assertEqual(json.loads(request.data)['model'], 'test-model')

    def test_long_chapter_preserved_and_new_characters_join_existing(self):
        original = ('李明说道：“你好。”王芳说：“明天见。”\n' * 800)
        prompts = []
        def reply(messages):
            prompt = json.loads(messages[-1]['content'])
            prompts.append(prompt)
            return json.dumps({'characters': ['王芳'], 'annotations': [
                {'id': row['id'], 'speaker': '王芳' if row['kind'] == 'dialogue' else None,
                 'emotion': 'happy', 'text': '绝不能替换原文'} for row in prompt['segments']]})
        analyzer = self.analyzer()
        with patch.object(analyzer, '_chat', side_effect=reply):
            segments, names = analyzer.analyze_chapter(original, ['李明'])
        self.assertGreater(len(prompts), 1)
        self.assertEqual(''.join(x.text for x in segments), ''.join(x.text for x in segment_text(original)))
        self.assertEqual([item['name'] for item in names], ['李明', '王芳'])
        self.assertTrue(all(x.speaker == '王芳' for x in segments if x.kind == 'dialogue'))

    def test_missing_annotations_rejected(self):
        analyzer = self.analyzer()
        with patch.object(analyzer, '_chat', return_value='{"characters": [], "annotations": []}'):
            with self.assertRaisesRegex(RuntimeError, 'JSON'):
                analyzer.analyze_chapter('这是原文。')

    def test_anonymous_role_is_scoped_to_chapter(self):
        analyzer = self.analyzer()
        def reply(messages):
            rows = json.loads(messages[-1]['content'])['segments']
            return json.dumps({'characters': [{'name': '城门守卫1', 'anonymous': True}],
                'annotations': [{'id': r['id'], 'speaker': '城门守卫1'} for r in rows],
                'mentioned_characters': ['城门守卫1']})
        with patch.object(analyzer, '_chat', side_effect=reply):
            one, characters = analyzer.analyze_chapter('“站住。”', chapter_id='ch1')
            two, _ = analyzer.analyze_chapter('“站住。”', characters, chapter_id='ch2')
        self.assertEqual(one[0].speaker, 'ch1·城门守卫1')
        self.assertEqual(two[0].speaker, 'ch2·城门守卫1')

    def test_errors_do_not_expose_key(self):
        for error, message in ((HTTPError('url', 401, 'test-secret', {}, None), '密钥无效'),
                               (URLError('test-secret'), '无法连接')):
            with patch('novel_studio.api_analysis.urlopen', side_effect=error):
                with self.assertRaises(RuntimeError) as result:
                    self.analyzer().test_connection()
                self.assertIn(message, str(result.exception))
                self.assertNotIn('test-secret', str(result.exception))

    def test_analysis_timeout_is_distinguished_from_bad_connection(self):
        analyzer = self.analyzer()
        with patch('novel_studio.api_analysis.urlopen', side_effect=TimeoutError()):
            with self.assertRaisesRegex(RuntimeError, '响应超过 120 秒'):
                analyzer._chat([{'role': 'user', 'content': 'slow'}])

    def test_annotation_numeric_values_are_clamped_to_engine_ranges(self):
        analyzer = self.analyzer()
        response = {'characters': [], 'annotations': [
            {'id': 0, 'kind': 'narration', 'speaker': None, 'pace': 0.8,
             'emotion_strength': 0.9, 'pause_after': 2.0}
        ]}
        with patch.object(analyzer, '_chat', return_value=json.dumps(response, ensure_ascii=False)):
            segments, _ = analyzer.analyze_chapter('这是正文。')
        self.assertEqual((segments[0].pace, segments[0].emotion_strength, segments[0].pause_after),
                         (0.85, 0.8, 1.5))

    def test_bad_configuration(self):
        with self.assertRaises(ValueError):
            self.analyzer('file:///tmp/example')
        with self.assertRaises(ValueError):
            APINovelAnalyzer('https://example.invalid', '', '')

    def test_inflight_request_is_closed_promptly_when_cancelled(self):
        cancel = threading.Event()
        closed = threading.Event()
        class SlowResponse:
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
            def read(self, *_):
                closed.wait(3)
                raise OSError('connection closed')
            def close(self): closed.set()
        analyzer = self.analyzer()
        with patch('novel_studio.api_analysis.urlopen', return_value=SlowResponse()):
            caller = threading.Thread(target=lambda: self._expect_cancel(analyzer, cancel))
            caller.start()
            time.sleep(.05)
            started = time.monotonic()
            cancel.set()
            caller.join(1)
            self.assertFalse(caller.is_alive(), 'cancel should return without waiting for the API timeout')
            self.assertLess(time.monotonic() - started, .8)
            self.assertTrue(closed.is_set())

    @staticmethod
    def _expect_cancel(analyzer, cancel):
        try:
            analyzer._chat([{'role': 'user', 'content': 'slow'}], cancel=cancel)
        except AnalysisCancelled:
            return
        raise AssertionError('request was not cancelled')


if __name__ == '__main__':
    unittest.main()
