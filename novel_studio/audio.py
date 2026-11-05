"""Small, dependency-light audio helpers for the chapter mixdown."""

from __future__ import annotations

import math
from pathlib import Path
import shutil
import subprocess
import wave


def read_wav(path: str | Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("音频混音目前只接受 16-bit PCM WAV。")
        channels = source.getnchannels()
        rate = source.getframerate()
        raw = source.readframes(source.getnframes())
    samples: list[float] = []
    for i in range(0, len(raw), 2 * channels):
        # Fold stereo to mono for deterministic ducking and export.
        frame = [int.from_bytes(raw[j : j + 2], "little", signed=True) / 32768 for j in range(i, i + 2 * channels, 2)]
        samples.append(sum(frame) / len(frame))
    return rate, samples


def write_wav(path: str | Path, sample_rate: int, samples: list[float]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pcm = bytearray()
    for sample in samples:
        value = max(-1.0, min(1.0, sample))
        pcm.extend(int(value * 32767).to_bytes(2, "little", signed=True))
    with wave.open(str(target), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


def resample(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if source_rate == target_rate or not samples:
        return samples
    output_count = max(1, round(len(samples) * target_rate / source_rate))
    scale = source_rate / target_rate
    result = []
    for i in range(output_count):
        position = i * scale
        left = min(len(samples) - 1, int(position))
        right = min(len(samples) - 1, left + 1)
        fraction = position - left
        result.append(samples[left] * (1 - fraction) + samples[right] * fraction)
    return result


def process_voice(samples: list[float], sample_rate: int = 24000, reverb: bool = False) -> list[float]:
    """Gentle high-pass and compression; optional quiet room reflections."""
    if not samples:
        return samples
    highpassed = []
    previous_x = previous_y = 0.0
    alpha = math.exp(-2 * math.pi * 75 / sample_rate)
    for sample in samples:
        current = alpha * (previous_y + sample - previous_x)
        highpassed.append(current)
        previous_x, previous_y = sample, current
    threshold = 10 ** (-20 / 20)
    compressed = []
    for sample in highpassed:
        magnitude = abs(sample)
        if magnitude > threshold:
            magnitude = threshold + (magnitude - threshold) / 2.5
        compressed.append(math.copysign(magnitude, sample))
    if reverb:
        delay = max(1, round(sample_rate * 0.075))
        for i in range(len(compressed)):
            if i >= delay:
                compressed[i] += compressed[i - delay] * 0.12
            if i >= delay * 2:
                compressed[i] += compressed[i - delay * 2] * 0.06
    peak = max((abs(x) for x in compressed), default=0.0)
    if peak > 0.92:
        scale = 0.92 / peak
        compressed = [sample * scale for sample in compressed]
    return compressed


def mix_layers(
    voice: list[float],
    beds: list[tuple[list[float], float, float]],
    voice_gain: float = 1.0,
    duck_db: float = -12.0,
    window_ms: int = 80,
    sample_rate: int = 24000,
) -> list[float]:
    """Mix (samples, gain, start_seconds) layers with speech-aware background ducking."""
    length = max([len(voice)] + [len(samples) + round(start * sample_rate) for samples, _, start in beds])
    mixed = [0.0] * length
    for i, sample in enumerate(voice):
        mixed[i] += sample * voice_gain
    window = max(1, round(sample_rate * window_ms / 1000))
    duck = 10 ** (duck_db / 20)
    voice_active = [False] * length
    for start in range(0, len(voice), window):
        frame = voice[start : start + window]
        rms = math.sqrt(sum(x * x for x in frame) / max(1, len(frame)))
        if rms > 0.015:
            voice_active[start : min(length, start + window)] = [True] * min(window, length - start)
    for samples, gain, offset in beds:
        start_at = max(0, round(offset * sample_rate))
        for i, sample in enumerate(samples):
            target = start_at + i
            if target >= length:
                break
            layer_gain = gain * (duck if target < len(voice_active) and voice_active[target] else 1.0)
            mixed[target] += sample * layer_gain
    peak = max((abs(x) for x in mixed), default=0.0)
    if peak > 0.98:
        scale = 0.98 / peak
        mixed = [x * scale for x in mixed]
    return mixed


def export_mp3(wav_path: str | Path, mp3_path: str | Path, bitrate: str = "192k") -> None:
    from .mastering import ffmpeg_path
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("未检测到 FFmpeg。请安装 FFmpeg 后再导出 MP3；WAV 导出不受影响。")
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-1200:] or "MP3 转换失败。")

