"""Who can see which LMS chunk. Needs a site with LMS + gdn_rag installed."""

import frappe
from frappe.tests.utils import FrappeTestCase

from gdn_rag.rag import indexer, retriever
from gdn_rag.rag.config import get_settings
from gdn_rag.tests import lms_fixtures as fx


def ask(user, question, scope=None):
	hits, _debug = retriever.retrieve(user, question, get_settings(), scope)
	return hits


def titles(hits):
	return {h.title for h in hits}


class TestLMSAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.cleanup()
		cls.teacher = fx.make_user("teacher")
		cls.member = fx.make_user("member")
		cls.stranger = fx.make_user("stranger")
		cls.moderator = fx.make_user("moderator", roles=["Moderator"])
		cls.sysmgr = fx.make_user("sysmgr", roles=["System Manager"])

		# course A: published, one preview lesson and one members-only lesson
		cls.course_a = fx.make_course("Alpha Course", cls.teacher)
		_, cls.lessons_a = fx.add_chapter(
			cls.course_a,
			"Basics",
			[
				{"title": "Free sample", "preview": 1, "content": fx.paragraphs("zebra stripes are free to read")},
				{"title": "Members only", "preview": 0, "content": fx.paragraphs("the wombat secret is for members")},
			],
		)
		# course B: published, members-only lesson, nobody is enrolled
		cls.course_b = fx.make_course("Bravo Course", cls.teacher)
		fx.add_chapter(cls.course_b, "Deep", [{"title": "Hidden bravo", "preview": 0, "content": fx.paragraphs("quokka facts")}])
		# course C: NOT published
		cls.course_c = fx.make_course("Charlie Course", cls.teacher, published=0)
		fx.add_chapter(cls.course_c, "Draft", [{"title": "Draft lesson", "preview": 1, "content": fx.paragraphs("narwhal draft notes")}])

		fx.enroll(cls.member, cls.course_a)
		frappe.db.commit()
		indexer.reindex_all("lms")
		frappe.db.commit()
		fx.forget_access(cls.teacher, cls.member, cls.stranger, cls.moderator, cls.sysmgr, "Administrator")

	@classmethod
	def tearDownClass(cls):
		fx.cleanup()
		super().tearDownClass()

	# ---- members
	def test_enrolled_student_sees_members_only_lesson(self):
		self.assertTrue(ask(self.member, "wombat secret"))

	def test_stranger_does_not_see_members_only_lesson(self):
		self.assertEqual(ask(self.stranger, "wombat secret"), [])

	def test_stranger_sees_preview_lesson_of_published_course(self):
		hits = ask(self.stranger, "zebra stripes")
		self.assertTrue(hits)
		self.assertTrue(all(h.scope_name == self.course_a for h in hits))

	def test_stranger_sees_course_overview_and_outline(self):
		hits = ask(self.stranger, "Bravo Course")
		self.assertTrue(any(h.ref_doctype == "LMS Course" and h.scope_name == self.course_b for h in hits))
		# the outline shows lesson titles, but not the lesson text
		self.assertEqual(ask(self.stranger, "quokka"), [])

	def test_member_of_one_course_does_not_see_another_course(self):
		self.assertEqual(ask(self.member, "quokka"), [])

	# ---- unpublished
	def test_unpublished_course_is_invisible_to_students(self):
		self.assertEqual(ask(self.stranger, "narwhal"), [])
		self.assertEqual(ask(self.member, "narwhal"), [])
		# "course" matches the other (published) courses, but never the unpublished one
		for question in ("Charlie Course", "Charlie", "Draft lesson"):
			self.assertFalse([h for h in ask(self.stranger, question) if h.scope_name == self.course_c], question)

	def test_unpublished_course_is_visible_to_its_instructor(self):
		self.assertTrue(ask(self.teacher, "narwhal"))

	def test_instructor_sees_members_only_lessons_of_own_courses(self):
		self.assertTrue(ask(self.teacher, "quokka"))
		self.assertTrue(ask(self.teacher, "wombat"))

	# ---- moderators and admins
	def test_moderator_sees_everything(self):
		for word in ("narwhal", "quokka", "wombat", "zebra"):
			self.assertTrue(ask(self.moderator, word), word)

	def test_administrator_counts_as_moderator(self):
		self.assertTrue(ask("Administrator", "narwhal"))

	def test_system_manager_without_moderator_role_gets_no_extra_access(self):
		self.assertEqual(ask(self.sysmgr, "wombat"), [])
		self.assertEqual(ask(self.sysmgr, "narwhal"), [])

	# ---- scope
	def test_scope_limits_the_search_to_one_course(self):
		scope = {"source": "lms", "scope_doctype": "LMS Course", "scope_name": self.course_a}
		hits = ask(self.teacher, "course", scope)
		self.assertTrue(hits)
		self.assertTrue(all(h.scope_name == self.course_a for h in hits))

	def test_scope_does_not_unlock_a_course_the_user_cannot_see(self):
		scope = {"source": "lms", "scope_doctype": "LMS Course", "scope_name": self.course_c}
		self.assertEqual(ask(self.stranger, "narwhal", scope), [])

	def test_enrollment_change_applies_after_the_access_cache_is_cleared(self):
		fx.enroll(self.stranger, self.course_b)
		frappe.db.commit()
		self.assertTrue(ask(self.stranger, "quokka"))
		for name in frappe.get_all("LMS Enrollment", filters={"member": self.stranger}, pluck="name"):
			frappe.delete_doc("LMS Enrollment", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		fx.forget_access(self.stranger)
		self.assertEqual(ask(self.stranger, "quokka"), [])

	# ---- private data is never indexed
	def test_private_fields_are_not_in_the_index(self):
		rows = frappe.db.sql(
			"SELECT COUNT(*) FROM `__gdn_rag_chunk` WHERE content LIKE %s OR search_text LIKE %s",
			("%SECRET-INSTRUCTOR%", "%secret instructor%"),
		)
		self.assertEqual(rows[0][0], 0)
		# even a moderator cannot find it: the number 9431 only exists inside the private note
		self.assertEqual(ask(self.moderator, "9431"), [])

	def test_lesson_fields_read_by_the_source_have_no_private_field(self):
		from gdn_rag.sources import lms

		for field in lms.LESSON_FIELDS:
			self.assertNotIn("instructor", field)
