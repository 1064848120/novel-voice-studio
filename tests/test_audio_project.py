import math
import tempfile
import unittest
from pathlib import Path

from novel_studio.audio import mix_layers, resample, write_wav
from novel_studio.project import add_character, bind_reference_audio, create_project, export_is_allowed, load_project, save_project
from novel_studio.sound_design import generate_preview_library, recommend_cues
from novel_studio.engines import index_model_revision, validate_index_model


class AudioAndProjectTests(unittest.TestCase):
    def test_index_model_preflight_and_revision_use_local_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "不会自动补齐"):
                validate_index_model(root)
            for name in ("config.yaml", "codec.pth", "gpt.pth", "s2mel.pth", "feat1.pt", "feat2.pt",
                         "wav2vec2bert_stats.pt", "multilingual_zh_ja_yue_char_del.tiktoken"):
                (root / name).write_bytes(b"ok")
            aux = root / "hf_cache"
            required = ["w2v-bert-2.0/config.json", "w2v-bert-2.0/model.safetensors",
                        "w2v-bert-2.0/preprocessor_config.json", "w2v-bert-2.0/conformer_shaw.pt",
                        "semantic_codec_model.safetensors", "campplus_cn_common.bin",
                        "bigvgan/config.json", "bigvgan/bigvgan_generator.pt"]
            for relative in required:
                path = aux / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"ok")
            qwen = root / "qwen0.6bemo4-merge"
            qwen.mkdir()
            for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
                (qwen / name).write_text("{}", encoding="utf-8")
            (root / "revision.json").write_text(
                '{"repo":"IndexTeam/IndexTTS-2.5","revision":"fixed-revision"}', encoding="utf-8"
            )
            resolved, paths = validate_index_model(root)
            self.assertEqual(resolved, root.resolve())
            self.assertTrue(Path(paths["w2v_bert"]).is_dir())
            self.assertEqual(index_model_revision(root), "fixed-revision")

    def test_ducking_and_resampling(self):
        rate = 1000
        voice = [0.3 * math.sin(2 * math.pi * 120 * i / rate) for i in range(1000)]
        bed = [0.2] * 1000
        mixed = mix_layers(voice, [(bed, 1.0, 0.0)], sample_rate=rate, duck_db=-20)
        self.assertLess(sum(abs(x) for x in mixed[:800]) / 800, 0.45)
        self.assertEqual(len(resample([0.0] * 100, 1000, 2000)), 200)

    def test_generated_effect_library_and_story_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = generate_preview_library(tmp, sample_rate=8000)
            self.assertGreaterEqual(len(files), 7)
            self.assertTrue(all(Path(item["path"]).is_file() for item in files))
            cues = recommend_cues("窗外下雨，他敲门后走进房间。")
            self.assertIn("rain", [item["id"] for item in cues])
            self.assertIn("knock", [item["id"] for item in cues])

    def test_character_voice_profile_is_stable_and_export_is_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "story.txt"
            source.write_text("第1章\n“你好。”", encoding="utf-8")
            project_root = root / "projects"
            folder = create_project(source, project_root)
            data = load_project(folder)
            character = add_character(data, "阿青", "女声")
            ref = root / "reference.wav"
            write_wav(ref, 8000, [0.05] * 100)
            bind_reference_audio(folder, character, ref)
            save_project(folder, data)
            loaded = load_project(folder)
            profile = loaded["characters"][0]["voice_profile"]
            self.assertEqual(profile["emotion_policy"], "prosody-only")
            self.assertEqual(profile["reference_sha256"], character["voice_profile"]["reference_sha256"])
            segment = loaded["chapters"][0]["segments"][0]
            segment["audio_path"] = "audio/ch-0001/0001.wav"
            segment["review_state"] = "unverified"
            allowed, pending = export_is_allowed({"chapters": [loaded["chapters"][0]]})
            self.assertFalse(allowed)
            self.assertEqual(len(pending), 1)
            segment["review_state"] = "approved"
            self.assertTrue(export_is_allowed({"chapters": [loaded["chapters"][0]]})[0])


if __name__ == "__main__":
    unittest.main()
