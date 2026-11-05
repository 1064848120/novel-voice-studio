import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import json

from novel_studio.voice_design import design_voice, describe_voice


class VoiceDesignTests(unittest.TestCase):
    def test_design_has_no_reference_and_records_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            python=root/'python.exe'; python.touch()
            runtime=root/'runtime'; runtime.mkdir(); (runtime/'infer.py').touch()
            model=root/'model'; model.mkdir(); (model/'audio_tokenizer').mkdir()
            for name in ('config.json', 'tokenizer.json', 'audio_tokenizer/model.safetensors', 'part.safetensors'):
                (model/name).write_text('{}',encoding='utf-8')
            (model/'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'a': 'part.safetensors'}}),encoding='utf-8')
            calls=[]
            def run(args, **kwargs):
                calls.append(args)
                if args[0]=='nvidia-smi':
                    return SimpleNamespace(returncode=0,stdout='10000')
                self.assertNotIn('--ref-audio', args)
                self.assertIn('--instruction', args)
                Path(args[args.index('--output')+1]).write_bytes(b'generated-wav')
                return SimpleNamespace(returncode=0)
            with patch('novel_studio.voice_design.subprocess.run',side_effect=run):
                path, recipe=design_voice(python,runtime,model,root/'out',{'gender':'男声','age':'青年','timbre':'低沉'})
            self.assertTrue(Path(path).is_file())
            self.assertTrue(Path(path).with_suffix('.json').is_file())
            self.assertIn('男声',recipe['design_prompt'])
            self.assertEqual(recipe['cfg_scale'],4)
            self.assertIn('reference_transcript',recipe)

    def test_missing_environment_reports_without_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'环境未就绪'):
                design_voice(Path(tmp)/'missing.exe',tmp,tmp,tmp,{})


if __name__=='__main__':
    unittest.main()
