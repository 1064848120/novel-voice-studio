"""Breeze designs a fixed voice asset for subsequent Breeze Voice Direction."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

ANCHOR_TEXT = '你好，很高兴在这里与你相遇。窗外的风渐渐停了，让我们坐下来，慢慢讲述今天的故事。'


def describe_voice(portrait):
    details = '，'.join(str(portrait.get(k, '')).strip() for k in ('gender', 'age', 'timbre', 'personality')
                       if portrait.get(k) not in (None, '', '未指定'))
    return f'一位{details or "声音自然清晰的成年人"}。{portrait.get("description", "")} 使用标准普通话，保持自然、清晰、稳定的个人声线，平和地说话，不要配乐和音效。'


def design_voice(python, runtime, models, destination, portrait, progress=None):
    python, runtime, models = Path(python).resolve(), Path(runtime).resolve(), Path(models).resolve()
    if not python.is_file() or not (runtime / 'infer.py').is_file():
        raise RuntimeError('Breeze 运行环境未就绪，请检查声音与模型中的路径。')
    for relative in ('config.json', 'model.safetensors.index.json', 'tokenizer.json', 'audio_tokenizer/model.safetensors'):
        if not (models / relative).is_file():
            raise RuntimeError(f'Breeze 模型尚未下载完整，缺少 {relative}。')
    index = json.loads((models / 'model.safetensors.index.json').read_text(encoding='utf-8'))
    if any(not (models / name).is_file() for name in set(index['weight_map'].values())):
        raise RuntimeError('Breeze 模型分片尚未下载完成。')
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and int(result.stdout.splitlines()[0]) < 8500:
            raise RuntimeError('Breeze 设计声音需要约 8.5GB 空闲显存。请先关闭 IndexTTS 网页服务或其他占用显存的程序，再重试。')
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    design_id = uuid4()
    seed = design_id.int % 2147483647
    output = destination / f'{design_id}.wav'
    description = describe_voice(portrait)
    args = [str(python), '-u', str(runtime / 'infer.py'), str(models), '--text', ANCHOR_TEXT,
            '--instruction', description, '--cfg-scale', '4', '--seed', str(seed), '--output', str(output)]
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    if progress:
        progress('Breeze 正在设计声音，首次加载需要等待…')
    log = destination / f'{design_id}.log'
    with log.open('w', encoding='utf-8') as stream:
        try:
            process = subprocess.run(args, cwd=runtime, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                     timeout=600, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'声音设计超过 10 分钟，任务已停止。日志：{log}') from None
    if process.returncode or not output.is_file():
        raise RuntimeError(f'Breeze 声音设计失败，未更改角色绑定。日志：{log}\n' + log.read_text(encoding='utf-8')[-1800:])
    revision_file = models / 'revision.json'
    metadata = {'source_engine': 'breeze-tts-2', 'design_prompt': description, 'seed': seed,
                'reference_transcript': ANCHOR_TEXT, 'cfg_scale': 4,
                'license': 'BreezeBlue Research and Non-Commercial License（本地生成限非商业使用）',
                'model_revision': json.loads(revision_file.read_text(encoding='utf-8')) if revision_file.is_file() else None}
    output.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(output), metadata
