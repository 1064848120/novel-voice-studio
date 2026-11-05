import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from novel_studio.importers import (Segment, decode_text, load_novel, merge_short_segments,
                                    repair_analyzed_segments, segment_text, split_chapters)


class ImporterTests(unittest.TestCase):
    def test_decodes_common_chinese_encodings(self):
        self.assertEqual(decode_text("你好".encode("gb18030")), "你好")
        self.assertEqual(decode_text(b"\xef\xbb\xbfHello"), "Hello")

    def test_chapter_split_and_dialogue_segments(self):
        parts = split_chapters("第1章 雨夜\n他看向门口。\n第2章 来客\n“谁？”")
        self.assertEqual([title for title, _ in parts], ["第1章 雨夜", "第2章 来客"])
        # Full-width Chinese quote marks split into separate dialogue blocks.
        segments = segment_text("他问：“你是谁？”她说：“朋友。”")
        self.assertEqual([s.kind for s in segments], ["narration", "dialogue", "narration", "dialogue"])
        self.assertIsNone(segments[1].speaker)

    def test_txt_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "story.txt"
            path.write_text("第1章 开始\n故事。", encoding="utf-8")
            novel = load_novel(path)
            self.assertEqual(novel.title, "story")
            self.assertEqual(len(novel.chapters), 1)
            self.assertEqual(novel.chapters[0].title, "第1章 开始")

    def test_long_paragraph_does_not_create_tiny_tail(self):
        segments = segment_text('甲' * 181)
        self.assertEqual(''.join(s.text for s in segments), '甲' * 181)
        self.assertTrue(all(len(s.text) >= 24 for s in segments))

    def test_merge_only_compatible_short_segments(self):
        narration = merge_short_segments([Segment('他走进房间。'), Segment('窗边没有人。')])
        self.assertEqual(len(narration), 1)
        uncertain_dialogue = merge_short_segments([
            Segment('嗯。', kind='dialogue'), Segment('走！', kind='dialogue')])
        self.assertEqual(len(uncertain_dialogue), 2)
        separate_speakers = merge_short_segments([
            Segment('等等。', kind='dialogue', speaker='甲'),
            Segment('快走！', kind='dialogue', speaker='乙')])
        self.assertEqual(len(separate_speakers), 2)

    def test_quoted_nouns_stay_in_narration(self):
        segments = segment_text('他把“装饰品”和“家具”全部卖出去。')
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].kind, 'narration')
        self.assertIn('装饰品', segments[0].text)
        spoken = segment_text('他问：“装饰品要留下吗？”')
        self.assertEqual([s.kind for s in spoken], ['narration', 'dialogue'])

    def test_repair_old_project_quoted_noun_fragments(self):
        text = '他把“装饰品”和“家具”全部卖出去。'
        segments = [
            {'text': '他把', 'kind': 'narration'},
            {'text': '装饰品', 'kind': 'dialogue', 'speaker': '甲'},
            {'text': '和', 'kind': 'narration', 'speaker': '__narrator__'},
            {'text': '家具', 'kind': 'dialogue', 'speaker': '甲'},
            {'text': '全部卖出去。', 'kind': 'narration'},
        ]
        self.assertTrue(repair_analyzed_segments(text, segments))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]['kind'], 'narration')
        self.assertIn('装饰品', segments[0]['text'])

    def test_epub_spine_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "story.epub"
            with ZipFile(path, "w", ZIP_DEFLATED) as epub:
                epub.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
                epub.writestr("OEBPS/content.opf", '<package><metadata><title>测试书</title></metadata><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
                epub.writestr("OEBPS/a.xhtml", "<html><body><h1>第一章</h1><p>先发生。</p></body></html>")
                epub.writestr("OEBPS/b.xhtml", "<html><body><p>后发生。</p></body></html>")
            novel = load_novel(path)
            self.assertEqual(novel.title, "测试书")
            self.assertEqual(len(novel.chapters), 2)
            self.assertIn("先发生", novel.chapters[0].text)
            self.assertIn("后发生", novel.chapters[1].text)


if __name__ == "__main__":
    unittest.main()
