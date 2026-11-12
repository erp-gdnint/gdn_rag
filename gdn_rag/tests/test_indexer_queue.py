import unittest
from unittest import mock

from gdn_rag.rag import indexer
from gdn_rag.rag.types import DocRef

REF = DocRef("lms", "Course Lesson", "L1")
CASCADE = DocRef("lms", "LMS Course", "C1", cascade=True)


class TestEnqueueRef(unittest.TestCase):
	def setUp(self):
		indexer.frappe.local.gdn_rag_scheduled = None
		self.enqueue = mock.patch.object(indexer.frappe, "enqueue").start()
		self.addCleanup(mock.patch.stopall)

	def status(self, mapping):
		return mock.patch.object(indexer, "_job_status", side_effect=lambda job_id: mapping.get(job_id))

	def test_nothing_queued_yet_queues_a_job(self):
		with self.status({}):
			indexer.enqueue_ref(REF)
		self.enqueue.assert_called_once()
		kwargs = self.enqueue.call_args.kwargs
		self.assertEqual(kwargs["job_id"], "gdn_rag:lms:Course Lesson:L1")
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual((kwargs["doctype"], kwargs["name"], kwargs["cascade"]), ("Course Lesson", "L1", False))

	def test_a_waiting_job_means_no_new_job(self):
		with self.status({"gdn_rag:lms:Course Lesson:L1": "queued"}):
			indexer.enqueue_ref(REF)
		self.enqueue.assert_not_called()

	def test_a_running_job_may_have_read_old_data_so_one_more_is_queued(self):
		with self.status({"gdn_rag:lms:Course Lesson:L1": "started"}):
			indexer.enqueue_ref(REF)
		self.assertEqual(self.enqueue.call_args.kwargs["job_id"], "gdn_rag:lms:Course Lesson:L1:again")

	def test_running_job_and_a_second_one_already_waiting(self):
		with self.status({"gdn_rag:lms:Course Lesson:L1": "started", "gdn_rag:lms:Course Lesson:L1:again": "queued"}):
			indexer.enqueue_ref(REF)
		self.enqueue.assert_not_called()

	def test_a_finished_job_does_not_block(self):
		with self.status({"gdn_rag:lms:Course Lesson:L1": "finished"}):
			indexer.enqueue_ref(REF)
		self.enqueue.assert_called_once()

	def test_same_document_twice_in_one_request_is_queued_once(self):
		# e.g. a course save and a chapter save in the same transaction (nothing is in Redis before the commit)
		with self.status({}):
			indexer.enqueue_ref(CASCADE)
			indexer.enqueue_ref(CASCADE)
		self.assertEqual(self.enqueue.call_count, 1)

	def test_cascade_and_plain_refs_are_different_jobs(self):
		course_plain = DocRef("lms", "LMS Course", "C1")
		with self.status({}):
			indexer.enqueue_ref(CASCADE)
			indexer.enqueue_ref(course_plain)
		ids = [c.kwargs["job_id"] for c in self.enqueue.call_args_list]
		self.assertEqual(ids, ["gdn_rag:lms:LMS Course:C1:cascade", "gdn_rag:lms:LMS Course:C1"])

	def test_redis_trouble_while_checking_does_not_stop_the_job_from_being_queued(self):
		with mock.patch("frappe.utils.background_jobs.get_job", side_effect=RuntimeError("redis down")):
			self.assertIsNone(indexer._job_status("x"))


if __name__ == "__main__":
	unittest.main()
