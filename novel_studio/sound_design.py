"""Scene cue suggestions and chapter rendering metadata."""

from __future__ import annotations

import math
import random
from pathlib import Path
import struct
import wave

from .audio import write_wav


CUES = {
    "rain": {"label": "雨声氛围", "terms": ("下雨", "雨声", "雨滴", "暴雨", "雨夜"), "kind": "ambience"},
    "wind": {"label": "风声氛围", "terms": ("风声", "狂风", "寒风", "山风", "呼啸"), "kind": "ambience"},
    "night": {"label": "夜晚虫鸣", "terms": ("夜晚", "夜色", "虫鸣", "夏夜", "夜里"), "kind": "ambience"},
    "forest": {"label": "林间氛围", "terms": ("树林", "森林", "鸟鸣", "林间"), "kind": "ambience"},
    "thunder": {"label": "远处雷声", "terms": ("打雷", "雷声", "惊雷", "雷电"), "kind": "effect"},
    "knock": {"label": "敲门音效", "terms": ("敲门", "叩门", "拍门"), "kind": "effect"},
    "footsteps": {"label": "脚步音效", "terms": ("脚步", "走近", "跑来", "脚步声"), "kind": "effect"},
    "door": {"label": "开关门音效", "terms": ("推开门", "关上门", "开门", "关门"), "kind": "effect"},
}


def recommend_cues(text: str) -> list[dict]:
    result = [{"id": "music", "name": "轻柔配乐氛围", "kind": "music", "position": 0.0, "gain": 0.08, "asset": None, "enabled": True}] if text.strip() else []
    for cue_id, spec in CUES.items():
        locations = [text.find(term) for term in spec["terms"] if term in text]
        if locations:
            result.append({
                "id": cue_id,
                "name": spec["label"],
                "kind": spec["kind"],
                "position": min(locations) / max(1, len(text)),
                "gain": 0.18 if spec["kind"] == "ambience" else 0.45,
                "asset": None,
                "enabled": True,
            })
    return sorted(result, key=lambda item: item["position"])


def generate_preview_library(root: str | Path, sample_rate: int = 24000) -> list[dict]:
    """Generate simple synthetic preview beds/effects; users can replace with licensed recordings."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    seconds = 5
    count = sample_rate * seconds
    random.seed(913)
    noise = [random.uniform(-1, 1) for _ in range(count)]
    rain = [0.08 * noise[i] + 0.04 * noise[i - 1] for i in range(count)]
    wind = []
    insect = []
    forest = []
    music = []
    for i in range(count):
        t = i / sample_rate
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 0.11 * t)
        wind.append(envelope * (0.09 * noise[i] + 0.08 * math.sin(2 * math.pi * 72 * t)))
        buzz = 0.045 * math.sin(2 * math.pi * 2200 * t) + 0.02 * math.sin(2 * math.pi * 3300 * t)
        insect.append(buzz * (0.5 + 0.5 * math.sin(2 * math.pi * 6 * t)))
        forest.append(0.025 * noise[i] + 0.018 * math.sin(2 * math.pi * (540 + 22 * math.sin(t)) * t))
        frequencies = (130.81, 164.81, 196.0, 261.63)
        music.append(sum(0.012 * math.sin(2 * math.pi * frequency * t) for frequency in frequencies))
    thunder = [0.13 * math.sin(2 * math.pi * (37 - 25 * i / count) * i / sample_rate) * math.exp(-5 * i / count) + 0.02 * noise[i] for i in range(count)]
    knock = [0.0] * count
    for hit in (int(0.4 * sample_rate), int(0.8 * sample_rate), int(1.2 * sample_rate)):
        for i in range(hit, min(count, hit + int(0.08 * sample_rate))):
            t = (i - hit) / sample_rate
            knock[i] += 0.3 * math.sin(2 * math.pi * 125 * t) * math.exp(-35 * t)
    footsteps = [0.0] * count
    for hit in (int(0.3 * sample_rate), int(0.8 * sample_rate), int(1.3 * sample_rate), int(1.8 * sample_rate)):
        for i in range(hit, min(count, hit + int(0.11 * sample_rate))):
            t = (i - hit) / sample_rate
            footsteps[i] += 0.2 * math.sin(2 * math.pi * 75 * t) * math.exp(-25 * t) + 0.03 * noise[i]
    door = [0.0] * count
    for i in range(int(0.15 * sample_rate), int(1.1 * sample_rate)):
        t = i / sample_rate
        door[i] = 0.1 * noise[i] * min(1.0, t * 8) * max(0.0, 1 - (t - 0.15) / 1.2)
    assets = {
        "rain": ("雨声氛围（合成预览）", rain, "ambience"),
        "wind": ("风声氛围（合成预览）", wind, "ambience"),
        "night": ("虫鸣氛围（合成预览）", insect, "ambience"),
        "forest": ("林间氛围（合成预览）", forest, "ambience"),
        "music": ("轻柔配乐草稿（合成预览）", music, "music"),
        "thunder": ("远雷音效（合成预览）", thunder, "effect"),
        "knock": ("敲门音效（合成预览）", knock, "effect"),
        "footsteps": ("脚步音效（合成预览）", footsteps, "effect"),
        "door": ("开关门音效（合成预览）", door, "effect"),
    }
    output = []
    for asset_id, (label, samples, kind) in assets.items():
        path = root / f"{asset_id}.wav"
        if not path.exists():
            write_wav(path, sample_rate, samples)
        output.append({"id": asset_id, "name": label, "kind": kind, "path": str(path), "license": "程序合成预览音频"})
    return output

