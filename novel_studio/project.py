"""Local project persistence and stable per-character voice profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from .importers import ImportedNovel, load_novel, novel_to_dict


PROJECT_FILE = "project.json"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", value, flags=re.UNICODE).strip("-_")
    return cleaned[:64] or "audiobook-project"


def create_project(source_path: str | Path, projects_root: str | Path) -> Path:
    novel = load_novel(source_path)
    root = Path(projects_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f"{slugify(novel.title)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir()
    (folder / "audio").mkdir()
    (folder / "voices").mkdir()
    (folder / "sounds").mkdir()
    shutil.copy2(novel.source_path, folder / Path(novel.source_path).name)
    chapters = novel_to_dict(novel)["chapters"]
    for chapter in chapters:
        chapter['production_state'] = '待分析'
        chapter['production_issues'] = []
    data = {
        "schema_version": 1,
        "id": str(uuid4()),
        "title": novel.title,
        "source_name": Path(novel.source_path).name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "engine": {"tts": "breeze-tts-2", "analysis": "newapi"},
        "narrator_voice_id": None,
        "characters": [],
        "sound_cues": [],
        "chapters": chapters,
    }
    save_project(folder, data)
    return folder


def load_project(folder: str | Path) -> dict[str, Any]:
    path = Path(folder) / PROJECT_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("项目格式版本不受支持。")
    return data


def save_project(folder: str | Path, data: dict[str, Any]) -> None:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    target = root / PROJECT_FILE
    temporary = root / (PROJECT_FILE + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def add_character(data: dict[str, Any], name: str, gender: str = "未指定") -> dict[str, Any]:
    existing = next((item for item in data["characters"] if item["name"] == name), None)
    if existing:
        return existing
    character = {
        "id": str(uuid4()),
        "name": name.strip(),
        "gender": gender,
        "tier": "C",
        "voice_profile": {
            "source": None,
            "reference_audio": None,
            "reference_sha256": None,
            "engine": "breeze-tts-2",
            "model_revision": None,
            "emotion_policy": "prosody-only",
            "settings": {"speed": 1.0, "emotion_strength": 0.35, "pause_scale": 1.0},
        },
    }
    data["characters"].append(character)
    return character


def bind_reference_audio(folder: str | Path, character: dict, source: str | Path, source_kind: str = "uploaded") -> None:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    import hashlib

    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    target_name = f"{character['id']}{source_path.suffix.lower()}"
    target = Path(folder) / "voices" / target_name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    profile = character["voice_profile"]
    profile["source"] = source_kind
    profile["reference_audio"] = str(Path("voices") / target_name)
    profile["reference_sha256"] = digest
    profile['sample_approved'] = False
    profile['reference_transcript'] = ''
    profile['model_revision'] = None


def export_is_allowed(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Require an explicit review for every rendered segment that lacks a verified score."""
    pending: list[str] = []
    for chapter in data.get("chapters", []):
        for index, segment in enumerate(chapter.get("segments", []), 1):
            if (not segment.get("audio_path") or segment.get("review_state") not in {"passed", "approved"}
                    or (segment.get('quality') or {}).get('technical', {}).get('state') == 'failed'):
                pending.append(f"{chapter.get('title', '章节')} · 第 {index} 段")
    return not pending, pending

