import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json

from novel_studio.audio import write_wav
from novel_studio.casting import import_voice, auto_cast, demote_uncertain_speakers, merge_characters, load_library
from novel_studio.project import save_project, load_project
from novel_studio.api_analysis import APINovelAnalyzer
from novel_studio.expression import expression_parameters
from novel_studio.engines import IndexTTS2Engine


class CastingTests(unittest.TestCase):
    def test_cast_stable_after_restart_and_trait_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / 'library'
            for i, (name, gender, role) in enumerate([('男青年', '男声', '角色'), ('女青年', '女声', '角色'), ('讲述者', '男声', '旁白')]):
                source = root / f'{i}.wav'
                write_wav(source, 8000, [.01 * (i+1)] * 16000)
                import_voice(library, source, {'name': name, 'gender': gender, 'age': '青年', 'role': role})
            project = {'schema_version': 1, 'characters': [], 'chapters': []}
            merge_characters(project, [{'name': '阿明', 'gender': '男声', 'age': '青年'}, {'name': '小芳', 'gender': '女声', 'age': '青年'}])
            assigned, pending = auto_cast(project, root, library)
            self.assertEqual(len(assigned), 3)
            self.assertEqual(pending, [])
            self.assertEqual(project['characters'][0]['voice_profile']['voice_traits']['gender'], '男声')
            fixed = copy.deepcopy(project['characters'])
            save_project(root, project)
            reopened = load_project(root)
            merge_characters(reopened, [{'name': '阿明', 'gender': '女声', 'age': '老年'}])
            auto_cast(reopened, root, library)
            self.assertEqual(reopened['characters'], fixed)
            merge_characters(reopened, [{'name': '另一个男人', 'gender': '男声'}])
            assigned, pending = auto_cast(reopened, root, library)
            self.assertFalse(assigned)
            self.assertTrue(pending)
            self.assertFalse(reopened['characters'][-1]['voice_profile']['reference_audio'])

    def test_character_traits_and_line_expression(self):
        analyzer = APINovelAnalyzer('https://example.invalid', 'test', 'test')
        def reply(messages):
            rows = json.loads(messages[-1]['content'])['segments']
            return json.dumps({'characters': [{'name': '小明', 'gender': '男声', 'age': '少年',
                'timbre': '清亮', 'personality': '活泼', 'aliases': ['阿明']}],
                'annotations': [{'id': row['id'], 'speaker': '阿明' if row['kind'] == 'dialogue' else None,
                                 'emotion': 'happy', 'emotion_strength': .6, 'pace': 1.1, 'pause_after': .4} for row in rows]})
        with patch.object(analyzer, '_chat', side_effect=reply):
            segments, portraits = analyzer.analyze_chapter('小明说：“太好了！”')
        self.assertEqual(portraits[0]['age'], '少年')
        self.assertEqual(segments[-1].speaker, '小明')
        self.assertEqual(segments[-1].emotion_strength, .6)

    def test_emotion_changes_keep_same_reference_at_engine_boundary(self):
        from unittest.mock import Mock
        engine = IndexTTS2Engine.__new__(IndexTTS2Engine)
        engine.model = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / 'fixed.wav'
            output = Path(tmp) / 'output.wav'
            reference.write_bytes(b'reference')
            engine.model.infer.side_effect = lambda **kw: Path(kw['output_path']).write_bytes(b'audio')
            for emotion in ('happy', 'angry', 'sad', 'calm'):
                engine.render('你好', reference, output, emotion, .5)
            calls = [c.kwargs for c in engine.model.infer.call_args_list]
            self.assertEqual({c['spk_audio_prompt'] for c in calls}, {str(reference)})
            self.assertTrue(all(c['use_random'] is False for c in calls))
            self.assertEqual(len({tuple(c['emo_vector']) for c in calls}), 4)
        with self.assertRaises(ValueError):
            expression_parameters('happy', float('nan'))

    def test_uncertain_speaker_is_safe_narration_for_auto_mode(self):
        project = {'characters': [{'name': '甲', 'id': 'a'}], 'chapters': [{
            'id': 'ch-1', 'segments': [
                {'text': '不确定', 'kind': 'dialogue', 'speaker': '甲', 'speaker_confidence': .4},
                {'text': '确定', 'kind': 'dialogue', 'speaker': '甲', 'speaker_confidence': .9},
            ]}]}
        changed = demote_uncertain_speakers(project)
        self.assertEqual(len(changed), 1)
        self.assertEqual(project['chapters'][0]['segments'][0]['kind'], 'narration')
        self.assertEqual(project['chapters'][0]['segments'][0]['speaker'], '__narrator__')
        self.assertEqual(project['chapters'][0]['segments'][1]['speaker'], '甲')


if __name__ == '__main__':
    unittest.main()
