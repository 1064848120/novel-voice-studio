"""Breeze Voice Direction jobs, fingerprints and resumable result manifests."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from .workflow import digest, profile_for


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def model_fingerprint(models, runtime):
    models, runtime = Path(models), Path(runtime)
    files = sorted([p for p in models.rglob('*') if p.is_file() and '.cache' not in p.parts
                    and (p.suffix in ('.safetensors', '.json')) and p.name != 'revision.json'])
    if not files or not (models / 'audio_tokenizer/model.safetensors').is_file():
        raise ValueError('Breeze 模型未完整安装。')
    files += sorted((runtime / 'models').rglob('*.py')) + sorted((runtime / 'breeze_infer').rglob('*.py'))
    cache = models / '.studio-fingerprint'
    signature = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    stamp = digest(signature)
    if cache.exists():
        old = json.loads(cache.read_text(encoding='utf-8'))
        if old.get('stamp') == stamp:
            return old['hash']
    value = digest([file_hash(p) for p in files])
    cache.write_text(json.dumps({'stamp': stamp, 'hash': value}), encoding='utf-8')
    return value


def instruction(item, retry=False):
    labels = {'neutral': '自然平静', 'happy': '开心', 'sad': '悲伤', 'angry': '愤怒',
              'afraid': '紧张害怕', 'surprised': '惊讶', 'calm': '沉静'}
    emotion = labels.get(item.get('emotion'), '自然平静')
    strength = float(item.get('emotion_strength', .35)) * (.65 if retry else 1)
    pace = float(item.get('pace', 1))
    if not math.isfinite(strength) or not math.isfinite(pace):
        raise ValueError('情绪参数无效。')
    level = '轻微、克制' if strength < .3 else '自然适中' if strength < .6 else '较明显但不夸张'
    speed = '略慢，字句清楚' if pace < .97 else '略快但不抢字' if pace > 1.03 else '自然语速'
    return f'保持参考说话人的音色、年龄感和声线身份不变。用{emotion}的情绪表达，强度{level}，{speed}。只朗读给定文字，不增添笑声、台词或背景声音。'


def technical_check(path, text=''):
    import numpy as np
    import wave
    try:
        with wave.open(str(path), 'rb') as wav:
            if wav.getsampwidth() != 2:
                raise ValueError('非 PCM16 音频')
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(float) / 32768
            duration = wav.getnframes() / wav.getframerate()
        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0
        clipped = float(np.mean(np.abs(audio) >= .999)) if audio.size else 0
        if duration < .15 or rms < .0005:
            raise ValueError('静音或音频过短')
        if clipped > .005:
            raise ValueError('存在明显削波失真')
        if text and (duration > max(8, len(text) * 1.5) or duration < len(text) / 30):
            raise ValueError('时长与台词长度差异异常，可能漏读或重复')
        return {'state': 'passed', 'duration': duration, 'rms': rms, 'clipped_ratio': clipped}
    except Exception as exc:
        return {'state': 'failed', 'message': str(exc)}


def collect_jobs(project, folder, indices, fingerprint, segment_indices=None):
    jobs = []
    folder = Path(folder)
    for ci in indices:
        chapter = project['chapters'][ci]
        for si, segment in enumerate(chapter['segments']):
            if segment_indices is not None and si not in segment_indices:
                continue
            profile = profile_for(project, segment)
            reference = folder / profile['reference_audio']
            actual = file_hash(reference)
            if actual != profile['reference_sha256']:
                raise ValueError('角色参考录音已被替换，请重新绑定并校对。')
            job = {k: segment.get(k, default) for k, default in [
                ('text', ''), ('emotion', 'neutral'), ('emotion_strength', .35), ('pace', 1.)]}
            job.update(reference_audio=str(reference), reference_transcript=profile['reference_transcript'],
                       seed=profile.get('seed', 42), reference_sha256=actual, model_revision=fingerprint,
                       settings=profile.get('settings', {}))
            if segment.get('render_take'):
                job['render_take'] = segment['render_take']
            job['signature'] = digest(job)
            job.update(chapter_index=ci, segment_index=si)
            target = folder / 'audio' / chapter['id'] / (job['signature'] + '.wav')
            job['output_path'] = str(target)
            if (segment.get('render_signature') == job['signature'] and segment.get('audio_path')
                    and (folder / segment['audio_path']).resolve() == target.resolve() and target.exists()
                    and segment.get('audio_sha256') == file_hash(target)):
                if segment.get('review_state') in ('passed', 'approved', 'unverified'):
                    continue
            jobs.append(job)
    return jobs


def render_queue(project, folder, indices, config, status, cancel):
    folder = Path(folder)
    status('正在检查固定声卡和模型文件，首次检查可能需要等待…')
    fingerprint = model_fingerprint(config['breeze_models'], config['breeze_runtime'])
    old = project.get('breeze_model_fingerprint')
    if old and old != fingerprint:
        raise ValueError('Breeze 模型或推理代码发生变化，为避免前后音色变化，请恢复原版本或创建新项目。')
    selected = config.get('segment_indices')
    if config.get('force'):
        for ci in indices:
            for si, segment in enumerate(project['chapters'][ci]['segments']):
                if selected is None or si in selected:
                    segment['render_take'] = uuid4().hex
    jobs = collect_jobs(project, folder, indices, fingerprint, selected)
    status({'detail': '任务队列已准备好，正在启动 Breeze…', 'done': 0, 'total': len(jobs), 'unit': '段'})
    jobs_dir = folder / 'jobs'
    jobs_dir.mkdir(exist_ok=True)
    request_file = jobs_dir / f'{uuid4()}.json'
    result_file = request_file.with_suffix('.result.json')
    cancel_file = request_file.with_suffix('.cancel')
    request_file.write_text(json.dumps({'model_dir': config['breeze_models'], 'runtime': config['breeze_runtime'],
        'fingerprint': fingerprint, 'segments': jobs, 'result_file': str(result_file),
        'cancel_file': str(cancel_file), 'asr_model': config.get('asr_model', '')}, ensure_ascii=False, indent=2), encoding='utf-8')
    if not jobs:
        return {'rows': [], 'fingerprint': fingerprint, 'message': '现有配音未变化，无需重做。'}
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
    import threading
    done = threading.Event()
    def watch_cancel():
        while not done.wait(.3):
            if cancel.is_set():
                cancel_file.touch()
                return
    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
    log = request_file.with_suffix('.log')
    try:
        with log.open('w', encoding='utf-8') as output:
            process = subprocess.Popen([config['breeze_python'], '-u', '-m', 'novel_studio.breeze_worker', str(request_file)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            for line in process.stdout:
                output.write(line)
                output.flush()
                if line.startswith('PROGRESS:'):
                    message = line[9:].strip()
                    if message.startswith('{'):
                        status(json.loads(message))
                    else:
                        status(message)
            code = process.wait()
    finally:
        done.set()
    payload = json.loads(result_file.read_text(encoding='utf-8')) if result_file.exists() else {'rows': []}
    payload.update(fingerprint=fingerprint, message=payload.get('error') or ('已停止，已生成片段保留。' if cancel.is_set() else '队列处理完成。'))
    if code and not payload['rows']:
        raise RuntimeError(f'Breeze 生成失败：{payload.get("error", "请查看日志")}\n{log}')
    return payload


def apply_results(project, folder, payload):
    project['breeze_model_fingerprint'] = payload['fingerprint']
    for row in payload['rows']:
        ch = project['chapters'][row['chapter_index']]
        segment = ch['segments'][row['segment_index']]
        segment.update(audio_path=str(Path(row['audio_path']).relative_to(folder)) if row.get('audio_path') else None,
                       render_signature=row['signature'], quality=row['quality'], review_state=row['review_state'],
                       model_revision=payload['fingerprint'], attempts=row.get('attempts', 1),
                       audio_sha256=row.get('audio_sha256'))
        if row.get('render_take'):
            segment['render_take'] = row['render_take']
        ch['audio_path'] = None
    project.setdefault('engine', {})['tts'] = 'breeze-tts-2'
