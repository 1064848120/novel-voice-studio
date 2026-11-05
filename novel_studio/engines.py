"""Optional local model setup and engine adapters.

The app never silently changes engine or speaker identity. Missing runtimes are reported as
unavailable so the project remains editable without weights or a CUDA environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Callable
import json
from .expression import expression_parameters


@dataclass
class DeviceInfo:
    nvidia_available: bool
    gpu_name: str | None = None
    vram_mb: int | None = None
    message: str = ""


def detect_device() -> DeviceInfo:
    executable = "nvidia-smi"
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            check=True,
        )
        row = result.stdout.strip().splitlines()[0]
        name, vram = row.rsplit(",", 1)
        return DeviceInfo(True, name.strip(), int(vram.strip()), "已检测到 NVIDIA GPU")
    except Exception:
        return DeviceInfo(False, message="未检测到可用的 NVIDIA GPU；本地 TTS 可能无法运行。")


def optional_runtime_status() -> dict[str, str]:
    status = {}
    for module, label in (("llama_cpp", "llama.cpp Python 运行库"), ("huggingface_hub", "文本模型下载组件")):
        try:
            __import__(module)
            status[label] = "已安装"
        except ImportError:
            status[label] = "未安装"
    return status


def download_hf_model(
    repo_id: str,
    destination: str | Path,
    progress: Callable[[str], None] | None = None,
    allow_patterns: list[str] | None = None,
) -> Path:
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as exc:
        raise RuntimeError("请先安装 requirements.txt 中的 huggingface_hub。") from exc
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    revision = HfApi().model_info(repo_id).sha
    if progress:
        progress(f"正在下载 {repo_id}（版本 {revision[:10]}）…")
    # HF verifies file hashes from repository metadata during download.
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=str(destination), allow_patterns=allow_patterns)
    if not any(p.is_file() for p in destination.rglob("*")):
        raise RuntimeError("模型下载目录为空。")
    (destination / "_commit_hash").write_text(revision, encoding="utf-8")
    if progress:
        progress(f"模型已保存到 {destination}")
    return destination


INDEX_MODEL_FILES = (
    "config.yaml", "codec.pth", "gpt.pth", "s2mel.pth", "feat1.pt", "feat2.pt",
    "wav2vec2bert_stats.pt", "multilingual_zh_ja_yue_char_del.tiktoken",
)


def validate_index_model(model_dir: str | Path) -> tuple[Path, dict[str, str]]:
    """Validate an already-present IndexTTS 2.5 installation without attempting downloads."""
    root = Path(model_dir).expanduser().resolve()
    missing = [name for name in INDEX_MODEL_FILES if not (root / name).is_file() or (root / name).stat().st_size == 0]
    aux = root / "hf_cache"
    required_aux = {
        "w2v_bert": ("w2v-bert-2.0/config.json", "w2v-bert-2.0/model.safetensors",
                     "w2v-bert-2.0/preprocessor_config.json", "w2v-bert-2.0/conformer_shaw.pt"),
        "campplus": ("campplus_cn_common.bin",),
        "bigvgan": ("bigvgan/config.json", "bigvgan/bigvgan_generator.pt"),
    }
    for files in required_aux.values():
        missing.extend(
            str(Path("hf_cache") / name)
            for name in files
            if not (aux / name).is_file() or (aux / name).stat().st_size == 0
        )
    qwen_dir = root / "qwen0.6bemo4-merge"
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
        path = qwen_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(str(Path("qwen0.6bemo4-merge") / name))
    if not (root / "revision.json").is_file() and not (root / "_commit_hash").is_file():
        missing.append("revision.json 或 _commit_hash（用于固定模型版本）")
    if missing:
        raise RuntimeError(
            "选中的 IndexTTS 2.5 模型不完整；为避免重复下载，程序不会自动补齐。\n缺少："
            + "、".join(missing)
            + "\n请选择已完成主模型和辅助模型下载的目录。"
        )
    paths = {
        "w2v_bert": str(aux / "w2v-bert-2.0"),
        "semantic_codec": str(root / "codec.pth"),
        "campplus": str(aux / "campplus_cn_common.bin"),
        "bigvgan": str(aux / "bigvgan"),
    }
    return root, paths


def index_model_revision(model_dir: str | Path) -> str:
    root = Path(model_dir).expanduser().resolve()
    metadata = root / "revision.json"
    if metadata.is_file():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            revision = payload.get("revision")
            if revision:
                return str(revision)
        except (OSError, json.JSONDecodeError):
            pass
    marker = root / "_commit_hash"
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else "local-unpinned"


class IndexTTS2Engine:
    """Thin explicit adapter; requires a separately installed official IndexTTS runtime."""

    engine_id = "index-tts-2"

    def __init__(self, model_dir: str | Path, config_path: str | Path | None = None):
        model_dir, _ = validate_index_model(model_dir)
        try:
            from indextts.infer_v2_5 import IndexTTS2
        except ImportError as exc:
            raise RuntimeError("IndexTTS 2 运行环境未安装。请按 README 的本机 GPU 安装步骤配置。") from exc
        model_dir = Path(model_dir).expanduser().resolve()
        config = Path(config_path).expanduser().resolve() if config_path else model_dir / "config.yaml"
        self.model = IndexTTS2(
            cfg_path=str(config), model_dir=str(model_dir), use_cuda_kernel=False,
            use_qwen_emo=False,
        )
        self.model_revision = index_model_revision(model_dir)

    @staticmethod
    def _revision(model_dir: Path) -> str:
        return index_model_revision(model_dir)

    def render(self, text: str, reference_audio: str | Path, output_path: str | Path, emotion: str = "neutral",
               strength: float = .35, pace: float = 1., settings: dict | None = None) -> Path:
        reference = Path(reference_audio).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError(f"角色参考音频不存在：{reference}")
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same reference and model for every segment; emotion is a prosody hint only.
        self.model.infer(
            spk_audio_prompt=str(reference),
            text=text,
            output_path=str(target),
            lang="ZH",
            **expression_parameters(emotion, strength, pace, settings),
        )
        if not target.is_file():
            raise RuntimeError("IndexTTS 没有生成目标音频。")
        return target

