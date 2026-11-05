"""Two-pass loudness normalization and chapter-marked M4B exports."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave
from uuid import uuid4


def export_chapter_audio(project, folder, index, status):
    """Run mixing and encoding off the GUI thread using a project snapshot."""
    from .audio import read_wav, resample, process_voice, mix_layers, write_wav, export_mp3
    from .breeze_render import collect_jobs
    from .project import export_is_allowed
    from .workflow import is_reviewed
    folder = Path(folder)
    chapter = project['chapters'][index]
    if (not is_reviewed(project, chapter) or not export_is_allowed({'chapters': [chapter]})[0]
            or collect_jobs(project, folder, [index], project.get('breeze_model_fingerprint', ''))):
        raise ValueError('章节配音未完成校对、质检或已发生变化，请重新生成并检查。')
    status('正在拼接对白、停顿和音效…')
    voice, timings = [], []
    rate = 24000
    for segment in chapter['segments']:
        sr, samples = read_wav(folder / segment['audio_path'])
        samples = resample(samples, sr, rate)
        timings.append({'start': len(voice) / rate, 'duration': len(samples) / rate})
        voice.extend(samples)
        voice.extend([0.] * round(float(segment.get('pause_after', .25)) * rate))
    beds = []
    for cue in chapter.get('cues', []):
        if not cue.get('enabled') or not cue.get('asset'):
            continue
        path = folder / cue['asset']
        if not path.is_file() or path.suffix.lower() != '.wav':
            raise ValueError(f'启用的音效文件不可用：{path.name}')
        sr, samples = read_wav(path)
        samples = resample(samples, sr, rate)
        start = float(cue.get('position', 0)) * len(voice) / rate
        if cue.get('kind') in ('ambience', 'music') and samples:
            remaining = max(0, len(voice) - round(start * rate))
            samples = (samples * (remaining // len(samples) + 1))[:remaining]
        beds.append((samples, float(cue.get('gain', .15)), start))
    settings = project.get('audio_settings', {})
    voice = process_voice(voice, rate, bool(settings.get('reverb', False)))
    mixed = mix_layers(voice, beds, sample_rate=rate)
    directory = folder / 'exports' / chapter['id'] / str(uuid4())
    raw, master = directory / 'mix-original.wav', directory / 'master.wav'
    write_wav(raw, rate, mixed)
    status('正在测量响度并制作 WAV / MP3 章节母带…')
    normalize(raw, master, settings.get('lufs', -19), settings.get('denoise', False))
    export_mp3(master, master.with_suffix('.mp3'))
    return {'path': master, 'timings': timings}


def ffmpeg_path():
    binary = shutil.which('ffmpeg')
    if not binary:
        for candidate in [Path(r'F:\VoiceoverBuild-20261104\craft\bin\ffmpeg.exe'),
                          Path(r'F:\VoiceoverBuild-20261104\baseline\kdenlive-26.08.2_standalone\bin\ffmpeg.exe')]:
            if candidate.is_file():
                binary = str(candidate)
                break
    if not binary:
        raise RuntimeError('未找到 FFmpeg。请将 FFmpeg 加入 PATH，原始 WAV 已保留。')
    return binary


def _run(args):
    result = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-1800:] or 'FFmpeg 处理失败。')
    return result.stderr


def normalize(source, destination, target=-19, denoise=False):
    if target not in (-16, -19):
        raise ValueError('只支持 -16 或 -19 LUFS 预设。')
    binary = ffmpeg_path()
    pre = 'afftdn=nf=-25,' if denoise else ''
    first = _run([binary, '-hide_banner', '-i', str(source), '-af',
                  pre + f'loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'])
    matches = re.findall(r'\{\s*"input_i".*?\}', first, re.S)
    if not matches:
        raise RuntimeError('无法取得响度测量结果。')
    measured = json.loads(matches[-1])
    if any(measured[k] in ('-inf', 'inf', 'nan') for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')):
        raise RuntimeError('音频响度无法测量，请检查是否静音。')
    filters = pre + f'loudnorm=I={target}:TP=-1.5:LRA=11:linear=true:' + ':'.join(
        f'{key}={measured[value]}' for key, value in [('measured_I', 'input_i'), ('measured_TP', 'input_tp'),
            ('measured_LRA', 'input_lra'), ('measured_thresh', 'input_thresh'), ('offset', 'target_offset')])
    _run([binary, '-y', '-i', str(source), '-af', filters, '-ar', '24000', '-c:a', 'pcm_s16le', str(destination)])
    return measured


def _metadata(value):
    return str(value).replace('\\', '\\\\').replace('=', '\\=').replace(';', '\\;').replace('#', '\\#').replace('\n', ' ')


def export_m4b(chapters, destination, title):
    """Use only ordered, normalized chapter masters. Preserve chapter titles and offsets."""
    binary = ffmpeg_path()
    if not chapters:
        raise ValueError('没有可以导出的章节。')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-m4b-', dir=destination.parent) as temp:
        root = Path(temp)
        playlist = root / 'chapters.ffconcat'
        entries = ['ffconcat version 1.0']
        metadata = [';FFMETADATA1', 'title=' + _metadata(title)]
        frames = 0
        rate = 24000
        for name, path in chapters:
            with wave.open(str(path), 'rb') as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, rate):
                    raise ValueError('M4B 输入必须是工作台导出的 24kHz 单声道 PCM16 章节母带。')
                start = frames
                frames += source.getnframes()
                metadata.extend(['[CHAPTER]', f'TIMEBASE=1/{rate}', f'START={start}', f'END={frames}',
                                 'title=' + _metadata(name)])
            # Stream chapters into FFmpeg, avoiding a book-sized RIFF file and its 4GB limit.
            escaped = Path(path).resolve().as_posix().replace("'", "'\\''")
            entries.append("file '" + escaped + "'")
        playlist.write_text('\n'.join(entries), encoding='utf-8')
        info = root / 'chapters.txt'
        info.write_text('\n'.join(metadata), encoding='utf-8')
        temporary = root / 'book.m4b'
        _run([binary, '-y', '-f', 'concat', '-safe', '0', '-i', str(playlist), '-i', str(info),
              '-map', '0:a:0', '-map_metadata', '1', '-map_chapters', '1',
              '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(temporary)])
        shutil.move(str(temporary), str(destination))
