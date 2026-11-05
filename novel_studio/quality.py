"""Voice identity quality gate. Missing verification libraries never pass silently."""

from __future__ import annotations

from pathlib import Path
import numpy as np
from functools import lru_cache


def discover_asr():
    snapshots = Path.home() / '.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots'
    for candidate in sorted(snapshots.glob('*')):
        if (candidate / 'model.bin').is_file() and (candidate / 'config.json').is_file():
            return str(candidate)
    return ''


@lru_cache(maxsize=1)
def asr_model(path):
    from faster_whisper import WhisperModel
    return WhisperModel(path, device='cpu', compute_type='int8', local_files_only=True)


def normalize_transcript(text):
    import re
    import unicodedata
    from opencc import OpenCC
    text = OpenCC('t2s').convert(unicodedata.normalize('NFKC', text))
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()


def compare_transcript(path, expected, model_dir=None):
    if not model_dir:
        return {'state': 'unverified', 'message': '未配置本地转写校验模型，请试听确认台词完整。'}
    try:
        import difflib
        if not Path(model_dir).is_dir():
            raise ValueError('转写模型目录不存在')
        model = asr_model(str(model_dir))
        segments, _ = model.transcribe(str(path), language='zh')
        text = ''.join(s.text for s in segments)
        score = difflib.SequenceMatcher(None, normalize_transcript(expected), normalize_transcript(text)).ratio()
        return {'state': 'passed' if score >= .75 else 'failed', 'score': score, 'text': text,
                'message': '台词转写比对通过。' if score >= .75 else '转写与原文差异较大，请检查漏读或重复。'}
    except Exception as exc:
        return {'state': 'unverified', 'message': f'转写校验不可用，需人工试听：{exc}'}


def compare_voice(reference_path: str | Path, generated_path: str | Path, threshold: float = 0.72) -> dict:
    """Return a speaker-embedding similarity result when resemblyzer is installed."""
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError:
        return compare_campplus(reference_path, generated_path)
    try:
        encoder = VoiceEncoder(device="cpu")
        ref = encoder.embed_utterance(preprocess_wav(Path(reference_path)))
        out = encoder.embed_utterance(preprocess_wav(Path(generated_path)))
        score = float(np.dot(ref, out) / (np.linalg.norm(ref) * np.linalg.norm(out)))
        if score < threshold:
            return {"state": "failed", "score": score, "message": "音色与角色参考样本差异较大，请检查性别/身份是否漂移。"}
        return {"state": "passed", "score": score, "message": "角色音色相似度检查通过。"}
    except Exception as exc:
        return {"state": "unverified", "score": None, "message": f"声纹校验失败，需试听确认：{exc}"}


@lru_cache(maxsize=1)
def campplus_model():
    import sys
    import torch
    root = Path(r'C:\Users\10648\Documents\Codex\2026-11-04\x20-f-comfyui-aki-motiongit-template\work\indextts-runtime')
    weights = Path(r'F:\VoiceoverBuild-20261104\models\indextts-2.5\hf_cache\campplus_cn_common.bin')
    if not weights.is_file():
        raise FileNotFoundError('本机声纹模型不可用')
    if str(root) not in sys.path:
        sys.path.append(str(root))
    from indextts.s2mel.modules.campplus.DTDNN import CAMPPlus
    model = CAMPPlus(feat_dim=80, embedding_size=192)
    model.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True))
    return model.eval()


def compare_campplus(reference, generated, threshold=.65):
    try:
        import torch
        import torchaudio
        import soundfile as sf
        model = campplus_model()
        def embed(path):
            audio, sr = sf.read(path, dtype='float32', always_2d=True)
            if len(audio) / sr < 1.:
                raise ValueError('片段不足 1 秒，声纹结果不可靠')
            audio = torch.from_numpy(audio.mean(axis=1)).unsqueeze(0)
            if sr != 16000:
                audio = torchaudio.functional.resample(audio, sr, 16000)
            feat = torchaudio.compliance.kaldi.fbank(audio, num_mel_bins=80, dither=0, sample_frequency=16000)
            feat -= feat.mean(dim=0, keepdim=True)
            with torch.inference_mode():
                return model(feat.unsqueeze(0))
        score = float(torch.nn.functional.cosine_similarity(embed(reference), embed(generated)).item())
        return {'state': 'passed' if score >= threshold else 'failed', 'score': score, 'method': 'CAMPPlus',
                'message': '声纹相似度通过，仍建议试听。' if score >= threshold else '角色声纹差异较大，需要人工复核。'}
    except Exception as exc:
        return {'state': 'unverified', 'score': None, 'message': f'声纹校验不可用，需试听：{exc}'}

