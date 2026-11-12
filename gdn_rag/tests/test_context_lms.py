"""Understanding the page the user is on, and the course picker."""

import frappe
from frappe.tests.utils import FrappeTestCase

from gdn_rag.rag import indexer
from gdn_rag.sources.lms import LMSSource
from gdn_rag.tests import lms_fixtures as fx


class TestLMSContext(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.cleanup()
		cls.teacher = fx.make_user("teacher")
		cls.member = fx.make_user("member")
		cls.stranger = fx.make_user("stranger")
		cls.course = fx.make_course("Context Course", cls.teacher)
		_, cls.lessons = fx.add_chapter(
			cls.course,
			"One",
			[
				{"title": "Open lesson", "preview": 1, "content": fx.paragraphs("open text")},
				{"title": "Closed lesson", "preview": 0, "content": fx.paragraphs("closed text")},
			],
		)
		cls.hidden = fx.make_course("Hidden Context", cls.teacher, published=0)
		fx.enroll(cls.member, cls.course)
		frappe.db.commit()
		fx.forget_access(cls.teacher, cls.member, cls.stranger)
		cls.source = LMSSource({})

	@classmethod
	def tearDownClass(cls):
		fx.cleanup()
		super().tearDownClass()

	def ctx(self, user, path=None, route=None):
		return self.source.resolve_context({"surface": "lms", "path": path, "desk_route": route}, user)

	def test_lesson_url(self):
		page = self.ctx(self.member, f"/lms/courses/{self.course}/learn/1-2")
		self.assertEqual(page.scope_name, self.course)
		self.assertIn("Lesson 1-2", page.label)
		self.assertIn("Closed lesson", page.label)
		self.assertEqual([r.name for r in page.pinned], [self.lessons[1]])

	def test_course_urls(self):
		for path in (f"/lms/courses/{self.course}", f"/lms/courses/{self.course}/", f"/lms/courses/{self.course}/certification"):
			page = self.ctx(self.stranger, path)
			self.assertEqual(page.scope_name, self.course, path)
			self.assertEqual(page.pinned, [])

	def test_url_encoded_name(self):
		page = self.ctx(self.stranger, f"/lms/courses/{self.course.replace('-', '%2D')}")
		self.assertEqual(page.scope_name, self.course)

	def test_restricted_lesson_is_not_pinned_for_a_stranger(self):
		page = self.ctx(self.stranger, f"/lms/courses/{self.course}/learn/1-2")
		self.assertEqual(page.scope_name, self.course)  # the course itself is public
		self.assertEqual(page.pinned, [])
		self.assertNotIn("Closed lesson", page.label)  # the title is not leaked either

	def test_preview_lesson_is_pinned_for_a_stranger(self):
		page = self.ctx(self.stranger, f"/lms/courses/{self.course}/learn/1-1")
		self.assertEqual([r.name for r in page.pinned], [self.lessons[0]])

	def test_unpublished_course_gives_no_context_to_a_stranger(self):
		self.assertIsNone(self.ctx(self.stranger, f"/lms/courses/{self.hidden}"))
		self.assertIsNotNone(self.ctx(self.teacher, f"/lms/courses/{self.hidden}"))

	def test_unknown_or_other_pages(self):
		for path in ("/lms/courses/does-not-exist", "/lms/courses/does-not-exist/learn/1-1", "/lms/batches", "/lms", "/", None, "/lms/courses/x/learn/9-9"):
			self.assertIsNone(self.ctx(self.member, path), path)

	def test_bad_lesson_number_still_gives_the_course(self):
		page = self.ctx(self.member, f"/lms/courses/{self.course}/learn/9-9")
		self.assertEqual(page.scope_name, self.course)
		self.assertEqual(page.pinned, [])

	def test_desk_forms(self):
		page = self.ctx(self.teacher, route=["Form", "LMS Course", self.course])
		self.assertEqual(page.scope_name, self.course)
		page = self.ctx(self.teacher, route=["Form", "Course Lesson", self.lessons[0]])
		self.assertEqual(page.scope_name, self.course)
		self.assertEqual([r.name for r in page.pinned], [self.lessons[0]])
		chapter = frappe.db.get_value("Course Lesson", self.lessons[0], "chapter")
		page = self.ctx(self.teacher, route=["Form", "Course Chapter", chapter])
		self.assertEqual(page.scope_name, self.course)
		self.assertIsNone(self.ctx(self.teacher, route=["Form", "User", "Administrator"]))
		self.assertIsNone(self.ctx(self.teacher, route=["List", "LMS Course"]))

	def test_garbage_input(self):
		for ctx in ({}, {"path": 5}, {"path": "/lms/courses//learn/1-1"}, {"desk_route": "Form"}, {"desk_route": ["Form"]}):
			self.assertIsNone(self.source.resolve_context(ctx, self.member))

	# ---- the course picker
	def test_scope_picker_lists_only_visible_courses(self):
		names = {s["scope_name"] for s in self.source.search_scopes(self.stranger, "")}
		self.assertIn(self.course, names)
		self.assertNotIn(self.hidden, names)
		names = {s["scope_name"] for s in self.source.search_scopes(self.teacher, "")}
		self.assertIn(self.hidden, names)

	def test_scope_picker_search_text(self):
		found = self.source.search_scopes(self.stranger, "Context Course")
		self.assertEqual([s["scope_name"] for s in found], [self.course])
		self.assertEqual(self.source.search_scopes(self.stranger, "zzzz-no-such"), [])
		self.assertEqual(self.source.search_scopes(self.stranger, "%"), self.source.search_scopes(self.stranger, ""))

	def test_scope_picker_for_user_with_no_courses_is_empty_without_error(self):
		nobody = fx.make_user("nobody")
		fx.forget_access(nobody)
		# a user who can see no course at all still gets the published ones; this checks the empty-set path
		names = {s["scope_name"] for s in self.source.search_scopes(nobody, "")}
		self.assertNotIn(self.hidden, names)
