import json
import os
import unittest

from gdn_rag.rag.normalize import build_query, build_search_text, coverage, light_stem, normalize, title_overlap

with open(os.path.join(os.path.dirname(__file__), "..", "rag_config", "defaults.json")) as f:
	SEARCH = json.load(f)["search"]


class TestNormalize(unittest.TestCase):
	def test_aliases_keep_symbols(self):
		self.assertEqual(normalize("Learn C++ and C# today", SEARCH["aliases"]), "learn cplusplus and csharp today")
		self.assertEqual(normalize("node.js basics", SEARCH["aliases"]), "nodejs basics")

	def test_alias_only_whole_words(self):
		# "ai" inside "train" or "main" must not change
		self.assertEqual(normalize("train main", SEARCH["aliases"]), "train main")

	def test_alias_phrase_is_added_to_search_text(self):
		text = build_search_text("Intro to Artificial Intelligence", SEARCH)
		self.assertIn("artificialintelligence", text.split())
		self.assertIn("artificial", text.split())

	def test_alias_phrase_works_in_both_directions(self):
		text = build_search_text("Intro to AI", SEARCH).split()
		self.assertIn("artificialintelligence", text)
		self.assertIn("artificial", text)  # so the question "artificial intelligence" finds it
		self.assertIn("intelligence", text)

	def test_stemmer(self):
		for word in ("certificate", "certificates", "certification"):
			self.assertEqual(light_stem(word), "certific", word)
		self.assertEqual(light_stem("learning"), "learn")
		self.assertEqual(light_stem("courses"), "course")
		self.assertEqual(light_stem("animals"), "animal")  # not "anim": it would match "animation"
		self.assertEqual(light_stem("variables"), "variable")
		self.assertEqual(light_stem("data"), "data")
		self.assertGreaterEqual(len(light_stem("use")), 3)

	def test_question_becomes_prefix_query(self):
		q = build_query("What is a certificate?", SEARCH)
		self.assertEqual(q.boolean, "certific*")
		self.assertEqual(q.short_terms, [])

	def test_stopword_only_question_is_empty(self):
		self.assertTrue(build_query("what is the", SEARCH).empty)
		self.assertTrue(build_query("???", SEARCH).empty)

	def test_short_terms_go_to_regexp_list(self):
		q = build_query("JS and UI tips", SEARCH)
		self.assertEqual(sorted(q.short_terms), ["js", "ui"])
		self.assertEqual(q.boolean, "tips")  # a normal word: FULLTEXT is used too

	def test_alias_in_question(self):
		self.assertEqual(build_query("AI", SEARCH).boolean, "artificialintelligence*")
		self.assertIn("cplusplu*", build_query("C++ basics", SEARCH).boolean.split())

	def test_boolean_operators_are_removed(self):
		q = build_query('+evil -"quote" (x) ~y <z>', SEARCH)
		for ch in '+-"()~<>@':
			self.assertNotIn(ch, q.boolean)

	def test_about_is_a_stopword_for_questions_only(self):
		# the index keeps "about"; the question side drops it
		self.assertIn("about", normalize("About the author").split())
		self.assertEqual(build_query("about author", SEARCH).tokens, ["author"])

	def test_at_most_12_tokens(self):
		q = build_query(" ".join(f"word{i}x" for i in range(30)), SEARCH)
		self.assertEqual(len(q.tokens), 12)

	def test_coverage_counts_different_words_not_repeats(self):
		aliases = SEARCH["aliases"]
		tokens = ["elephants", "largest", "land", "animals"]
		repeats = "Animals animals animals. Learn about animals."
		answer = "Elephants are the largest land animals."
		self.assertEqual(coverage(tokens, repeats, aliases), 0.25)
		self.assertEqual(coverage(tokens, answer, aliases), 1.0)

	def test_coverage_prefix_and_short_words(self):
		self.assertEqual(coverage(["certific"], "the certification process"), 1.0)
		self.assertEqual(coverage(["js"], "learn js now"), 1.0)
		self.assertEqual(coverage(["js"], "json and jsx"), 0.0)  # a 2-letter word is a whole word
		self.assertEqual(coverage([], "anything"), 0.0)

	def test_title_overlap(self):
		self.assertEqual(title_overlap(["python"], "Course: Python 101", SEARCH["aliases"]), 1.0)
		self.assertEqual(title_overlap(["java"], "Course: Python 101", SEARCH["aliases"]), 0.0)


if __name__ == "__main__":
	unittest.main()
