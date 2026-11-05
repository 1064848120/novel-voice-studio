"""Local LLM analysis and a clearly labelled offline review fallback."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .importers import Segment, segment_text


class LocalNovelAnalyzer:
    def __init__(self, model_path: str | Path | None = None, n_ctx: int = 8192):
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.model = None
        self.status = "尚未配置本地文本模型"
        if self.model_path and self.model_path.is_file():
            try:
                from llama_cpp import Llama

                self.model = Llama(model_path=str(self.model_path), n_ctx=n_ctx, n_gpu_layers=-1, verbose=False)
                self.status = "本地模型已就绪"
            except ImportError:
                self.status = "未安装 llama-cpp-python；可继续手动校对"
            except Exception as exc:
                self.status = f"本地模型加载失败：{exc}"

    def close(self) -> None:
        model = self.model
        self.model = None
        if model is not None:
            del model
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    def analyze_chapter(self, text: str, known_characters: list[str] | None = None) -> tuple[list[Segment], list[str]]:
        if self.model is None:
            segments = segment_text(text)
            characters = _guess_names(text)
            return segments, characters
        prompt = {
            "task": "将中文小说拆分为可配音片段，识别说话人、情绪和人物。不要改写原文，不要新增台词。",
            "characters": known_characters or [],
            "schema": {"characters": ["人物名"], "segments": [{"text": "原文", "kind": "dialogue|narration", "speaker": "人物名或null", "emotion": "neutral|happy|sad|angry|afraid|surprised|calm"}]},
            "text": text[:18000],
        }
        response = self.model.create_chat_completion(
            messages=[
                {"role": "system", "content": "你是严谨的中文有声书文本分析器。只返回合法 JSON；不确定的说话人必须为 null，不得猜测。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        payload = _extract_json(response["choices"][0]["message"]["content"])
        segments: list[Segment] = []
        for row in payload.get("segments", []):
            value = str(row.get("text", "")).strip()
            if not value:
                continue
            segments.append(Segment(
                text=value,
                kind="dialogue" if row.get("kind") == "dialogue" else "narration",
                speaker=row.get("speaker") if row.get("speaker") in (known_characters or payload.get("characters", [])) else None,
                emotion=_normalize_emotion(row.get("emotion")),
            ))
        return segments or segment_text(text), [str(x) for x in payload.get("characters", []) if str(x).strip()]


def _extract_json(value: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", value, re.DOTALL)
    if not match:
        raise ValueError("本地模型没有返回 JSON 格式的识别结果。")
    return json.loads(match.group(0))


def _normalize_emotion(value: Any) -> str:
    allowed = {"neutral", "happy", "sad", "angry", "afraid", "surprised", "calm"}
    return value if value in allowed else "neutral"


def _guess_names(text: str) -> list[str]:
    # Conservative prompt-name guess; user must review these before assigning a voice.
    found: list[str] = []
    for match in re.finditer(r"(?:^|\n)\s*([\u4e00-\u9fff]{2,4})(?:说道|问道|答道|喊道|笑道|低声道|开口道)", text):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return found

