"""Search quality and indexing behaviour on MariaDB. Needs a site with LMS + gdn_rag installed."""

import frappe
from frappe.tests.utils import FrappeTestCase

from gdn_rag.rag import db, indexer, retriever
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.types import DocRef, PageContext
from gdn_rag.tests import lms_fixtures as fx


def build_query_for(question):
	from gdn_rag.rag.normalize import build_query

	return build_query(question, get_settings().search)


def ask(user, question, scope=None, context=None, history=None):
	hits, _ = retriever.retrieve(user, question, get_settings(), scope, context, history)
	return hits


class TestRetrieval(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.cleanup()
		cls.admin = "Administrator"
		cls.teacher = fx.make_user("teacher")
		cls.course = fx.make_course("Search Course", cls.teacher)
		_, cls.lessons = fx.add_chapter(
			cls.course,
			"Topics",
			[
				{"title": "About the author", "preview": 1, "content": fx.paragraphs("Hello. I will tell who wrote this course.")},
				{"title": "Certification process", "preview": 1, "content": fx.paragraphs("Pass the final exam to get your certification.")},
				{"title": "Languages", "preview": 1, "content": fx.paragraphs("We compare C++ and C# and node.js here.")},
				{"title": "Short words", "preview": 1, "content": fx.paragraphs("Learn JS and UI basics in this part.")},
				{"title": "Machines", "preview": 1, "content": fx.paragraphs("Intro to AI and machine learning.")},
				{"title": "Elephants", "preview": 1, "content": fx.paragraphs("Elephants are the largest land animals.")},
				{"title": "Long lesson", "preview": 1, "content": fx.paragraphs(*[f"Paragraph number {i} about gardening tools and soil. " * 6 for i in range(12)], header="Garden")},
			],
		)
		frappe.db.commit()
		indexer.reindex_all("lms")
		frappe.db.commit()
		fx.forget_access("Administrator")

	@classmethod
	def tearDownClass(cls):
		fx.cleanup()
		super().tearDownClass()

	def lesson_title_hits(self, question, **kw):
		return [h.title for h in ask(self.admin, question, **kw)]

	def assertFinds(self, question, lesson_title, **kw):
		titles = self.lesson_title_hits(question, **kw)
		self.assertTrue(any(lesson_title in t for t in titles), f"{question!r} -> {titles}")

	# ---- the MariaDB problems from plan section 6.1
	def test_stopword_probe_is_ok(self):
		self.assertEqual(db.state_get("stopword_probe"), "ok")

	def test_built_in_stopwords_are_searchable_in_the_index(self):
		# "about" and "will" and "who" are InnoDB stop words; the index must keep them
		rows = frappe.db.sql(
			"SELECT id FROM `__gdn_rag_chunk` WHERE MATCH(title, search_text) AGAINST ('wrote' IN BOOLEAN MODE)"
		)
		self.assertTrue(rows)
		rows = frappe.db.sql(
			"SELECT id FROM `__gdn_rag_chunk` WHERE MATCH(title, search_text) AGAINST ('will' IN BOOLEAN MODE)"
		)
		self.assertTrue(rows, "stop words were removed from the FULLTEXT index")

	def test_question_with_stopword_in_the_title(self):
		self.assertFinds("who wrote this", "About the author")
		self.assertFinds("about author", "About the author")

	def test_prefix_search_finds_other_word_forms(self):
		self.assertFinds("What is a certificate?", "Certification process")
		self.assertFinds("how do I get certified", "Certification process")

	def test_symbols_in_names(self):
		self.assertFinds("C++", "Languages")
		self.assertFinds("c# basics", "Languages")
		self.assertFinds("node.js", "Languages")

	def test_alias_for_two_letter_words(self):
		self.assertFinds("AI", "Machines")
		# the text says "AI" but the user types the long form
		self.assertFinds("artificial intelligence", "Machines")

	def test_short_words_use_the_regexp_scan(self):
		self.assertFinds("JS", "Short words")
		self.assertFinds("UI", "Short words")

	def test_short_word_does_not_match_inside_other_words(self):
		# "js" must not be found inside a longer word
		titles = self.lesson_title_hits("JS")
		self.assertFalse(any("Garden" in t for t in titles))

	def test_stopword_only_question_returns_nothing(self):
		self.assertEqual(ask(self.admin, "what is the"), [])
		self.assertEqual(ask(self.admin, "???"), [])

	def test_operators_in_the_question_cannot_break_the_query(self):
		for q in ('+certif -"exam"', "(((", "certification* ~exam <x> @y", "'; DROP TABLE x; --"):
			ask(self.admin, q)  # must not raise

	def test_follow_up_uses_the_previous_question(self):
		history = [{"role": "user", "content": "Tell me about certification"}, {"role": "assistant", "content": "ok"}]
		titles = [h.title for h in ask(self.admin, "and how long?", history=history)]
		self.assertTrue(any("Certification" in t for t in titles), titles)

	def test_pinned_lesson_comes_first(self):
		lesson = frappe.db.get_value("Course Lesson", {"course": self.course, "title": "Machines"}, "name")
		ctx = PageContext("lms", "LMS Course", self.course, "Course: x", [DocRef("lms", "Course Lesson", lesson)])
		hits = ask(self.admin, "something about certification", context=ctx)
		self.assertEqual(hits[0].ref_name, lesson)

	def test_top_k_and_context_limit(self):
		hits = ask(self.admin, "gardening tools soil")
		self.assertLessEqual(len(hits), int(get_settings().retrieval.top_k))
		self.assertLessEqual(sum(len(h.content) for h in hits), int(get_settings().retrieval.max_context_chars) + 2000)

	# ---- ranking
	def test_text_that_matches_every_word_beats_a_title_only_match(self):
		hits = ask(self.admin, "elephants largest land animals")
		self.assertEqual(hits[0].title.split("Lesson: ")[-1], "Elephants", [h.title for h in hits])

	def test_repeated_word_in_the_overview_does_not_beat_the_real_answer(self):
		# a course whose overview repeats "animals" many times (high database relevance, low coverage)
		teacher = self.teacher
		course = fx.make_course("Zoo Animals", teacher, short="Animals animals animals. Learn about animals and more animals.")
		fx.add_chapter(course, "Mammals", [{"title": "Free sample", "preview": 1, "content": fx.paragraphs("Okapis are the shyest forest animals.")}])
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", course, cascade=True)
		frappe.db.commit()
		scope = {"source": "lms", "scope_doctype": "LMS Course", "scope_name": course}
		hits = ask(self.admin, "okapis shyest forest animals", scope=scope)
		self.assertEqual(hits[0].title.split("Lesson: ")[-1], "Free sample", [h.title for h in hits])
		# and a question about the course itself still finds the overview first
		hits = ask(self.admin, "Zoo Animals", scope=scope)
		self.assertEqual(hits[0].ref_doctype, "LMS Course", [h.title for h in hits])

	def test_answer_survives_many_chunks_that_match_one_common_word(self):
		# 120 chunks that repeat "animals" crowd the top-30 of the database ranking. The chunk that has
		# ALL the question words must still be found (second query that requires every word).
		first = [h.title for h in ask(self.admin, "elephants largest land animals")][:1]
		self.assertTrue(first and first[0].endswith("Lesson: Elephants"), first)
		for i in range(120):
			frappe.db.sql(
				"""INSERT INTO `__gdn_rag_chunk` (chunk_key, source, ref_doctype, ref_name, seq, scope_doctype, scope_name,
				access, title, route, content, search_text, content_hash, modified)
				VALUES (%s, 'lms', 'Course Lesson', %s, 0, 'LMS Course', %s, 'public', %s, '/x', 'filler', %s, 'h', NOW(6))""",
				(f"lms|Course Lesson|{fx.PREFIX}-crowd-{i}|0", f"{fx.PREFIX}-crowd-{i}", self.course, f"Animals animals {i}", "animals animals animals animals animals animals"),
			)
		frappe.db.commit()
		try:
			hits = ask(self.admin, "elephants largest land animals")
			self.assertTrue(hits[0].title.endswith("Lesson: Elephants"), [h.title for h in hits])
		finally:
			frappe.db.sql("DELETE FROM `__gdn_rag_chunk` WHERE chunk_key LIKE %s", (f"lms|Course Lesson|{fx.PREFIX}-crowd-%",))
			frappe.db.commit()

	def test_animals_does_not_match_animation(self):
		self.assertEqual(build_query_for("animals").boolean, "animal*")

	def test_a_question_about_the_course_name_finds_the_course_overview_first(self):
		hits = ask(self.admin, "Search Course")
		self.assertEqual((hits[0].ref_doctype, hits[0].ref_name), ("LMS Course", self.course), [h.title for h in hits])

	def test_ranking_does_not_depend_on_index_statistics(self):
		# index many unrelated chunks, then ask again: the best answer must stay first
		first = [h.title for h in ask(self.admin, "elephants largest land animals")][:1]
		for i in range(30):
			frappe.db.sql(
				"""INSERT INTO `__gdn_rag_chunk` (chunk_key, source, ref_doctype, ref_name, seq, scope_doctype, scope_name,
				access, title, route, content, search_text, content_hash, modified)
				VALUES (%s, 'lms', 'Course Lesson', %s, 0, 'LMS Course', %s, 'public', %s, '/x', 'filler', 'animals filler words here', 'h', NOW(6))""",
				(f"lms|Course Lesson|{fx.PREFIX}-filler-{i}|0", f"{fx.PREFIX}-filler-{i}", self.course, f"Filler animals {i}"),
			)
		frappe.db.commit()
		try:
			self.assertEqual([h.title for h in ask(self.admin, "elephants largest land animals")][:1], first)
		finally:
			frappe.db.sql("DELETE FROM `__gdn_rag_chunk` WHERE chunk_key LIKE %s", (f"lms|Course Lesson|{fx.PREFIX}-filler-%",))
			frappe.db.commit()

	# ---- indexing behaviour
	def test_long_lesson_is_split_into_several_chunks(self):
		lesson = frappe.db.get_value("Course Lesson", {"course": self.course, "title": "Long lesson"}, "name")
		count = frappe.db.sql(
			"SELECT COUNT(*) FROM `__gdn_rag_chunk` WHERE ref_doctype='Course Lesson' AND ref_name=%s", (lesson,)
		)[0][0]
		self.assertGreater(count, 1)

	def test_unchanged_documents_are_not_rewritten(self):
		totals = indexer.reindex_one("lms", "LMS Course", self.course, force=False)
		self.assertEqual(totals["added"] + totals["updated"] + totals["deleted"], 0, totals)
		self.assertGreater(totals["unchanged"], 0)

	def test_route_format(self):
		hits = ask(self.admin, "certification")
		lesson_hits = [h for h in hits if h.ref_doctype == "Course Lesson"]
		self.assertTrue(lesson_hits)
		self.assertRegex(lesson_hits[0].route, rf"^/lms/courses/{self.course}/learn/\d+-\d+$")

	def test_edit_then_reindex_changes_the_search_result(self):
		lesson_name = frappe.db.get_value("Course Lesson", {"course": self.course, "title": "Machines"}, "name")
		doc = frappe.get_doc("Course Lesson", lesson_name)
		doc.content = fx.paragraphs("Now this lesson talks about volcanoes only.")
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		indexer.index_ref("lms", "Course Lesson", lesson_name)
		frappe.db.commit()
		self.assertFinds("volcanoes", "Machines")
		# the title still says "Machines", but the old body text is gone from the stored chunk
		stored = frappe.db.sql(
			"SELECT content FROM `__gdn_rag_chunk` WHERE ref_doctype='Course Lesson' AND ref_name=%s", (lesson_name,)
		)
		self.assertTrue(stored)
		for (content,) in stored:
			self.assertNotIn("learning", content)
			self.assertIn("volcanoes", content)

	def test_deleted_lesson_is_removed_from_the_index(self):
		_, names = fx.add_chapter(self.course, "Temp chapter", [{"title": "Temporary", "preview": 1, "content": fx.paragraphs("ephemeral xylophone")}])
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", self.course, cascade=True)
		frappe.db.commit()
		self.assertFinds("xylophone", "Temporary")
		frappe.delete_doc("Course Lesson", names[0], force=True, ignore_permissions=True)
		frappe.db.commit()
		indexer.index_ref("lms", "Course Lesson", names[0])
		frappe.db.commit()
		self.assertEqual(ask(self.admin, "xylophone"), [])

	def test_lesson_removed_from_its_chapter_is_pruned_by_the_course_job(self):
		chapter, names = fx.add_chapter(self.course, "Prune chapter", [{"title": "Pruneme", "preview": 1, "content": fx.paragraphs("platypus habitat")}])
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", self.course, cascade=True)
		frappe.db.commit()
		self.assertFinds("platypus", "Pruneme")
		chapter_doc = frappe.get_doc("Course Chapter", chapter)
		chapter_doc.lessons = []
		chapter_doc.save(ignore_permissions=True)
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", self.course, cascade=True)
		frappe.db.commit()
		self.assertEqual(ask(self.admin, "platypus"), [])

	def test_deleting_the_course_removes_all_its_chunks(self):
		teacher = self.teacher
		course = fx.make_course("Doomed", teacher)
		fx.add_chapter(course, "C", [{"title": "L", "preview": 1, "content": fx.paragraphs("doomed pangolin text")}])
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", course, cascade=True)
		frappe.db.commit()
		self.assertTrue(ask(self.admin, "pangolin"))
		for dt in ("Course Lesson", "Course Chapter"):
			for name in frappe.get_all(dt, filters={"course": course}, pluck="name"):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		frappe.delete_doc("LMS Course", course, force=True, ignore_permissions=True)
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", course, cascade=True)
		frappe.db.commit()
		left = frappe.db.sql("SELECT COUNT(*) FROM `__gdn_rag_chunk` WHERE scope_name=%s", (course,))[0][0]
		self.assertEqual(left, 0)
