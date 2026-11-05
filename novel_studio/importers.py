"""TXT and EPUB import, chapter splitting, and dialogue segmentation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable
from zipfile import ZipFile
import xml.etree.ElementTree as ET


@dataclass
class Segment:
    text: str
    kind: str = "narration"
    speaker: str | None = None
    speaker_reason: str = ''
    speaker_confidence: float = 0.0
    speaker_resolution: str = 'unknown'
    auto_demoted_to_narration: bool = False
    emotion: str = "neutral"
    emotion_strength: float = 0.35
    pace: float = 1.0
    pause_after: float = 0.25
    start: float = 0.0
    duration: float = 0.0
    voice_profile_id: str | None = None
    audio_path: str | None = None
    review_state: str = "not_rendered"


@dataclass
class Chapter:
    id: str
    title: str
    text: str
    segments: list[Segment] = field(default_factory=list)
    audio_path: str | None = None


@dataclass
class ImportedNovel:
    title: str
    source_path: str
    chapters: list[Chapter]


CHAPTER_RE = re.compile(
    r"^\s*(?P<title>(?:第[零〇一二三四五六七八九十百千万\d]+[章节回卷部篇].{0,40}|序章|楔子|番外(?:篇)?|后记|尾声|引子))\s*$",
    re.MULTILINE,
)
QUOTE_RE = re.compile(r'[“「『"](.*?)[”」』"]', re.DOTALL)
SPEECH_CUE_RE = re.compile(r'(?:说|道|问|答|回|喊|叫|骂|嘟囔|喃喃|笑着|喝道|说道|问道|回答|喊道)[：:，,。！!？?]?$')
DIALOGUE_PUNCTUATION = set('。！？；，、…—!?;,')


def is_dialogue_quote(text: str, match: re.Match) -> bool:
    """Distinguish spoken quotes from quoted nouns/titles in narration."""
    inner = match.group(1).strip()
    if not inner:
        return False
    before = text[max(0, match.start() - 28):match.start()]
    line_start = not before.strip() or before.rstrip().endswith(('\n', '\r'))
    has_speech_cue = bool(SPEECH_CUE_RE.search(before.rstrip()))
    has_dialogue_punctuation = any(mark in inner for mark in DIALOGUE_PUNCTUATION)
    # Short quoted nouns such as “装饰品”和“家具” belong to narration. A
    # line-start quote, a speech verb, or dialogue punctuation remains spoken.
    if not line_start and not has_speech_cue and not has_dialogue_punctuation and len(inner) <= 12:
        return False
    return True


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "h1", "h2", "h3", "br", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg", "head"}:
            self.skip_depth += 1
        elif tag in self.BLOCKS and self.skip_depth == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "head"} and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCKS and self.skip_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            self.parts.append(data)


def _epub_text(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """Return book title and ordered (spine item name, plain text) sections."""
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            (node.attrib.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")),
            None,
        )
        if not rootfile or rootfile not in names:
            raise ValueError("EPUB 中找不到内容目录文件（container.xml/rootfile）。")
        package = ET.fromstring(archive.read(rootfile))
        base = Path(rootfile).parent.as_posix()
        manifest: dict[str, str] = {}
        title = path.stem
        for node in package.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "item" and node.attrib.get("id") and node.attrib.get("href"):
                href = (Path(base) / node.attrib["href"]).as_posix() if base != "." else node.attrib["href"]
                manifest[node.attrib["id"]] = href
            elif tag == "title" and node.text and node.text.strip():
                title = node.text.strip()
        spine_ids = [n.attrib.get("idref") for n in package.iter() if n.tag.endswith("itemref")]
        sections: list[tuple[str, str]] = []
        for item_id in spine_ids:
            href = manifest.get(item_id or "")
            if not href or href not in names:
                continue
            parser = _TextExtractor()
            parser.feed(decode_text(archive.read(href)))
            plain = re.sub(r"[ \t\xa0]+", " ", "".join(parser.parts))
            plain = re.sub(r"\n\s*\n+", "\n", plain).strip()
            if plain:
                sections.append((Path(href).stem, plain))
        if not sections:
            raise ValueError("EPUB 中没有可读取的正文内容。")
        return title, sections


def split_chapters(text: str, fallback_title: str = "正文") -> list[tuple[str, str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    matches = list(CHAPTER_RE.finditer(text))
    if not matches:
        return [(fallback_title, text)] if text else []
    chapters: list[tuple[str, str]] = []
    prefix = text[: matches[0].start()].strip()
    if prefix:
        chapters.append(("序文", prefix))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            chapters.append((match.group("title").strip(), body))
    return chapters or [(fallback_title, text)]


def segment_text(text: str) -> list[Segment]:
    """Create reviewable narration/dialogue blocks without guessing a speaker."""
    result: list[Segment] = []
    cursor = 0
    narration_buffer = ''
    for match in QUOTE_RE.finditer(text):
        before = text[cursor : match.start()].strip()
        if not is_dialogue_quote(text, match):
            narration_buffer += (before + match.group(0))
            cursor = match.end()
            continue
        narration_buffer += before
        if narration_buffer.strip():
            result.append(Segment(narration_buffer.strip(), kind="narration"))
            narration_buffer = ''
        dialogue = match.group(1).strip()
        if dialogue:
            result.append(Segment(dialogue, kind="dialogue", speaker=None))
        cursor = match.end()
    narration_buffer += text[cursor:]
    if narration_buffer.strip():
        result.append(Segment(narration_buffer.strip(), kind="narration"))
    result = result or ([Segment(text.strip())] if text.strip() else [])
    short = []
    for segment in result:
        remaining = segment.text
        while len(remaining) > 180:
            # Balance the last two chunks so a 181-character paragraph does not end in a one-character tail.
            limit = min(180, len(remaining) - 24)
            punctuation = max(remaining.rfind(mark, 60, limit) for mark in '。！？；，,.!?;\n')
            end = punctuation + 1 if punctuation >= 0 else limit
            short.append(replace(segment, text=remaining[:end]))
            remaining = remaining[end:]
        if remaining:
            short.append(replace(segment, text=remaining))
    return short


def repair_analyzed_segments(text: str, segments: list[dict]) -> bool:
    """Repair old projects created before quoted-noun detection was added."""
    changed = False
    for segment in segments:
        if segment.get('kind') != 'dialogue' or not segment.get('text'):
            continue
        value = segment['text'].strip()
        if len(value) > 12 or any(mark in value for mark in DIALOGUE_PUNCTUATION):
            continue
        for match in QUOTE_RE.finditer(text):
            if match.group(1).strip() == value and not is_dialogue_quote(text, match):
                segment.update(kind='narration', speaker='__narrator__', speaker_reason='')
                segment.update(audio_path=None, render_signature=None, review_state='not_rendered', quality=None)
                changed = True
                break
    # Merge adjacent short narration records even when an old model assigned
    # slightly different emotions to them.
    merged = []
    for segment in segments:
        if (merged and segment.get('kind') == merged[-1].get('kind') == 'narration'
                and len(merged[-1].get('text', '')) + len(segment.get('text', '')) <= 180
                and (len(merged[-1].get('text', '')) < 24 or len(segment.get('text', '')) < 24)):
            merged[-1]['text'] += segment.get('text', '')
            merged[-1]['pause_after'] = segment.get('pause_after', merged[-1].get('pause_after', .25))
            merged[-1].update(audio_path=None, render_signature=None, review_state='not_rendered', quality=None)
            changed = True
        else:
            merged.append(segment)
    if changed:
        segments[:] = merged
    return changed


def merge_short_segments(segments):
    """Join adjacent short pieces only when speaker and expression are identical."""
    result = []
    keys = ('kind', 'speaker', 'emotion', 'emotion_strength', 'pace')
    for segment in segments:
        if (result and (len(result[-1].text) < 24 or len(segment.text) < 24)
                and len(result[-1].text) + len(segment.text) <= 180
                and all(getattr(result[-1], k) == getattr(segment, k) for k in keys)
                and (segment.kind == 'narration' or segment.speaker)):
            result[-1] = replace(result[-1], text=result[-1].text + segment.text, pause_after=segment.pause_after)
        else:
            result.append(replace(segment))
    return result


def load_novel(path: str | Path) -> ImportedNovel:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() == ".txt":
        title = source.stem
        raw = decode_text(source.read_bytes())
        chapter_pairs = split_chapters(raw, title)
    elif source.suffix.lower() == ".epub":
        title, sections = _epub_text(source)
        # EPUB spine documents are usually chapter files; retain their ordering and split further
        # when a file contains explicit chapter headings.
        chapter_pairs = []
        for label, body in sections:
            parts = split_chapters(body, label)
            if len(parts) == 1 and parts[0][0] == label:
                chapter_pairs.append((label, body))
            else:
                chapter_pairs.extend(parts)
    else:
        raise ValueError("当前只支持 TXT 和 EPUB 文件。")
    chapters = [
        Chapter(id=f"ch-{index:04d}", title=title_part, text=body, segments=segment_text(body))
        for index, (title_part, body) in enumerate(chapter_pairs, 1)
        if body.strip()
    ]
    if not chapters:
        raise ValueError("没有识别到小说正文。")
    return ImportedNovel(title=title, source_path=str(source), chapters=chapters)


def chapter_to_dict(chapter: Chapter) -> dict:
    return asdict(chapter)


def novel_to_dict(novel: ImportedNovel) -> dict:
    return {
        "title": novel.title,
        "source_path": novel.source_path,
        "chapters": [chapter_to_dict(chapter) for chapter in novel.chapters],
    }

