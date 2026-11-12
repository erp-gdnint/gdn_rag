import json
import unittest

from gdn_rag.rag.chunking import pack_sections, split_long
from gdn_rag.rag.text import Section, body_to_sections, editorjs_to_sections, html_to_text, lesson_sections


def blocks(*items):
	return json.dumps({"blocks": [{"type": t, "data": d} for t, d in items]})


class TestText(unittest.TestCase):
	def test_html_to_text(self):
		self.assertEqual(html_to_text("<p>Hello&nbsp;<b>world</b></p><p>Next</p>"), "Hello world\nNext")

	def test_editorjs_sections(self):
		content = blocks(
			("header", {"text": "Intro", "level": 2}),
			("paragraph", {"text": "Hello <b>there</b>"}),
			("list", {"items": ["a", {"content": "b", "items": ["c"]}]}),
			("header", {"text": "Code"}),
			("code", {"code": "print(1)"}),
			("quiz", {"quiz": "SECRET-QUIZ-ID"}),
			("table", {"content": [["x", "y"], ["1", "2"]]}),
		)
		sections = editorjs_to_sections(content)
		self.assertEqual([s.heading for s in sections], ["Intro", "Code"])
		self.assertIn("Hello there", sections[0].text)
		self.assertIn("- a", sections[0].text)
		self.assertIn("  - c", sections[0].text)
		self.assertIn("print(1)", sections[1].text)
		self.assertIn("x | y", sections[1].text)
		self.assertNotIn("SECRET", json.dumps([s.__dict__ for s in sections]))

	def test_invalid_json_falls_back_to_body(self):
		self.assertIsNone(editorjs_to_sections("not json"))
		sections = lesson_sections("not json", "# Title\nSome text {{ YouTubeVideo('abc') }}")
		self.assertEqual(sections[0].heading, "Title")
		self.assertNotIn("YouTube", sections[0].text)

	def test_empty(self):
		self.assertEqual(lesson_sections("", ""), [])
		self.assertEqual(body_to_sections(None), [])


class TestChunking(unittest.TestCase):
	def test_long_text_is_split_with_overlap(self):
		text = " ".join(f"sentence{i}." for i in range(400))
		pieces = split_long(text, 500, 50)
		self.assertGreater(len(pieces), 3)
		self.assertTrue(all(len(p) <= 500 for p in pieces))
		self.assertIn("sentence0.", pieces[0])
		self.assertIn("sentence399.", pieces[-1])

	def test_pack_small_sections_together(self):
		sections = [Section("A", "x" * 100), Section("B", "y" * 100)]
		self.assertEqual(len(pack_sections(sections, 1800, 200, 80)), 1)

	def test_pack_splits_when_full(self):
		sections = [Section(f"H{i}", "z" * 900) for i in range(4)]
		chunks = pack_sections(sections, 1800, 200, 80)
		self.assertEqual(len(chunks), 4)

	def test_tiny_chunk_is_merged(self):
		sections = [Section("A", "x" * 1400), Section("B", "tiny")]
		chunks = pack_sections(sections, 1405, 100, 80)
		self.assertEqual(len(chunks), 1)
		self.assertIn("tiny", chunks[0])

	def test_single_tiny_chunk_is_kept(self):
		self.assertEqual(pack_sections([Section("A", "hi")], 1800, 200, 80), ["A\nhi"])


if __name__ == "__main__":
	unittest.main()
