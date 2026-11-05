"""Isolated TTS process. Keeps CUDA memory separate from the desktop and text model."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

from .engines import IndexTTS2Engine, index_model_revision, validate_index_model


def run(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    model_dir, _ = validate_index_model(request["model_dir"])
    expected = request.get("expected_revision")
    revision = index_model_revision(model_dir)
    if expected and revision != expected:
        raise RuntimeError(
            "此项目固定使用的 IndexTTS 模型版本与当前模型不同。为避免章节间音色不一致，"
            "请恢复原模型或为新模型创建单独项目。"
        )
    # Prevalidate the v2.5 local files before instantiating its matching runtime.
    engine = IndexTTS2Engine(model_dir)
    results = []
    items = request.get("segments", [])
    for index, item in enumerate(items, 1):
        print(f"正在生成第 {index}/{len(items)} 段", flush=True)
        reference = Path(item['reference_audio'])
        expected_hash = item.get('reference_sha256')
        if expected_hash and hashlib.sha256(reference.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError('角色音色录音已被修改，生成已停止，请重新确认绑定。')
        path = engine.render(item["text"], reference, item["output_path"], item.get("emotion", "neutral"),
                             item.get('emotion_strength', .35), item.get('pace', 1.), item.get('settings'))
        results.append({"audio_path": str(path), "model_revision": engine.model_revision})
    print("RESULT:" + json.dumps(results, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m novel_studio.tts_worker REQUEST.json")
    raise SystemExit(run(sys.argv[1]))
