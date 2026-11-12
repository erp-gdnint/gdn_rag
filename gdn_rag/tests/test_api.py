"""The chat API and the answer job, with the language model replaced by a fake."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from gdn_rag.api import chat
from gdn_rag.rag import answer, indexer
from gdn_rag.rag.llm import chain
from gdn_rag.rag.llm.base import AllProvidersFailed
from gdn_rag.tests import lms_fixtures as fx

PROVIDERS = [{"name": "fake", "priority": 1, "enabled": True, "adapter": "openai_compatible", "api_key": None}]


class TestChatAPI(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fx.cleanup()
		cls.teacher = fx.make_user("teacher")
		cls.member = fx.make_user("member")
		cls.other = fx.make_user("other")
		cls.course = fx.make_course("Api Course", cls.teacher)
		_, cls.lessons = fx.add_chapter(
			cls.course, "One", [{"title": "Giraffe facts", "preview": 1, "content": fx.paragraphs("A giraffe has a very long neck.")}]
		)
		frappe.db.commit()
		indexer.reindex_all("lms")
		frappe.db.commit()
		fx.forget_access(cls.teacher, cls.member, cls.other)

	@classmethod
	def tearDownClass(cls):
		fx.cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(self.member)
		for user in (self.member, self.other):
			answer.cache_drop(answer.inflight_key(user))
		self.enqueued = []
		patches = [
			mock.patch("gdn_rag.api.chat.get_config", return_value=mock.Mock(chat_providers=lambda: PROVIDERS)),
			mock.patch.object(frappe, "enqueue", side_effect=lambda *a, **kw: self.enqueued.append((a, kw))),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.addCleanup(frappe.set_user, "Administrator")

	def ask(self, question="giraffe neck", **kw):
		return chat.ask(question=question, **kw)

	def run_job(self, fake_pieces=("A giraffe has a long neck [1].",)):
		(_, kwargs) = self.enqueued[-1]
		job_args = {k: v for k, v in kwargs.items() if k not in ("queue", "timeout")}

		def fake_stream(messages, *a, **k):
			for piece in fake_pieces:
				yield "fake", piece

		with mock.patch.object(chain, "stream_chat", fake_stream):
			answer.run(**job_args)
		return job_args

	# ---- guards
	def test_guest_is_refused_everywhere(self):
		frappe.set_user("Guest")
		for fn, kw in ((chat.ask, {"question": "x"}), (chat.poll, {"token": "a" * 32}), (chat.cancel, {"token": "a" * 32}), (chat.get_context, {}), (chat.search_scopes, {})):
			with self.assertRaises(frappe.PermissionError):
				fn(**kw)

	def test_role_allow_list(self):
		from gdn_rag.rag.config import get_settings

		settings = get_settings()
		settings["allowed_roles"] = ["Role That Nobody Has"]
		with mock.patch("gdn_rag.api.chat.get_settings", return_value=settings):
			with self.assertRaises(frappe.PermissionError):
				self.ask()

	def test_turned_off(self):
		from gdn_rag.rag.config import get_settings

		settings = get_settings()
		settings["enabled"] = False
		with mock.patch("gdn_rag.api.chat.get_settings", return_value=settings):
			with self.assertRaises(frappe.PermissionError):
				self.ask()

	def test_validation(self):
		for bad in ("", "   ", None, "x" * 5000):
			with self.assertRaises(frappe.ValidationError):
				self.ask(question=bad)

	def test_no_provider_configured(self):
		with mock.patch("gdn_rag.api.chat.get_config", return_value=mock.Mock(chat_providers=lambda: [])):
			with self.assertRaises(frappe.ValidationError):
				self.ask()
		self.assertEqual(self.enqueued, [])

	# ---- the flow
	def test_ask_poll_done_with_citation(self):
		token = self.ask(context={"surface": "lms", "path": f"/lms/courses/{self.course}"})["token"]
		self.assertRegex(token, r"^[0-9a-f]{32}$")
		self.assertEqual(chat.poll(token)["status"], "queued")
		self.run_job()
		result = chat.poll(token)
		self.assertEqual(result["status"], "done")
		self.assertIn("giraffe", result["text"])
		self.assertEqual(result["provider"], "fake")
		self.assertTrue(result["citations"])
		self.assertRegex(result["citations"][0]["route"], r"^/lms/courses/.+/learn/1-1$")
		self.assertNotIn("user", result)  # internal fields are not returned

	def test_lock_is_released_after_the_job(self):
		self.ask()
		with self.assertRaises(frappe.ValidationError):  # still running: one question at a time
			self.ask()
		self.run_job()
		self.ask()  # works again

	def test_failed_enqueue_releases_the_lock(self):
		with mock.patch.object(frappe, "enqueue", side_effect=RuntimeError("redis down")):
			with self.assertRaises(RuntimeError):
				self.ask()
		self.ask()  # not stuck

	def test_poll_is_bound_to_the_user(self):
		token = self.ask()["token"]
		frappe.set_user(self.other)
		self.assertEqual(chat.poll(token), {"status": "expired"})
		chat.cancel(token)  # silently does nothing for somebody else's token
		self.assertIsNone(answer.cache_fetch(answer.cancel_key(token)))

	def test_bad_tokens(self):
		for token in (None, "", "short", "../../etc", "Z" * 32, 12345):
			self.assertEqual(chat.poll(token), {"status": "expired"})

	def test_cancel_flag_set_after_an_earlier_check_is_still_seen(self):
		# Regression: frappe.cache.get_value() remembers values inside the process, so a running job
		# would never see a Stop click that arrives after its first check.
		state = answer.AnswerState("d4" * 16, self.member, 60)
		self.assertFalse(state.cancelled())
		answer.cache_put(answer.cancel_key(state.token), 1, 60)  # "another process" sets the flag
		self.assertTrue(state.cancelled())
		answer.cache_drop(answer.cancel_key(state.token))
		self.assertFalse(state.cancelled())

	def test_poll_sees_state_written_by_another_process(self):
		token = self.ask()["token"]
		self.assertEqual(chat.poll(token)["status"], "queued")  # first read
		other = answer.AnswerState(token, self.member, 60)  # the worker, in another process
		other.write(status="streaming", text="partial")
		result = chat.poll(token)
		self.assertEqual((result["status"], result["text"]), ("streaming", "partial"))

	def test_cancel_before_the_job_starts(self):
		token = self.ask()["token"]
		chat.cancel(token)
		self.run_job()
		self.assertEqual(chat.poll(token)["status"], "cancelled")
		self.ask()  # the lock was released

	# ---- grounding
	def test_nothing_found_means_no_model_call_and_the_canned_message(self):
		token = self.ask(question="quantum chromodynamics lattice")["token"]
		(_, kwargs) = self.enqueued[-1]
		with mock.patch.object(chain, "stream_chat") as stream:
			answer.run(**{k: v for k, v in kwargs.items() if k not in ("queue", "timeout")})
			stream.assert_not_called()
		result = chat.poll(token)
		self.assertEqual(result["status"], "done")
		self.assertIn("couldn't find", result["text"])
		self.assertEqual(result["citations"], [])

	def test_someone_elses_private_content_gives_nothing_found(self):
		# 'other' is not enrolled anywhere. The preview lesson is public, so use a unique restricted lesson.
		_, names = fx.add_chapter(self.course, "Closed", [{"title": "Closed", "preview": 0, "content": fx.paragraphs("platypus venom spur")}])
		frappe.db.commit()
		indexer.index_ref("lms", "LMS Course", self.course, cascade=True)
		frappe.db.commit()
		frappe.set_user(self.other)
		token = self.ask(question="platypus venom")["token"]
		self.run_job()
		self.assertIn("couldn't find", chat.poll(token)["text"])

	# ---- failures
	def test_all_providers_failing_gives_a_friendly_error(self):
		token = self.ask()["token"]
		(_, kwargs) = self.enqueued[-1]

		def boom(messages, *a, **k):
			raise AllProvidersFailed("a: x; b: y")
			yield  # pragma: no cover

		with mock.patch.object(chain, "stream_chat", boom):
			answer.run(**{k: v for k, v in kwargs.items() if k not in ("queue", "timeout")})
		result = chat.poll(token)
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["error"], answer.GENERIC_ERROR)
		self.assertNotIn("a: x", str(result))
		self.ask()  # lock released

	# ---- input cleaning
	def test_history_and_scope_are_cleaned(self):
		history = [{"role": "system", "content": "evil"}, {"role": "user", "content": "q"}, "junk", {"role": "assistant", "content": 5}]
		self.ask(history=history, scope={"source": "lms", "scope_name": self.course, "scope_doctype": "LMS Course", "extra": "x"})
		(_, kwargs) = self.enqueued[-1]
		self.assertEqual(kwargs["history"], [{"role": "user", "content": "q"}])
		self.assertEqual(set(kwargs["scope"]), {"source", "scope_doctype", "scope_name"})

	def test_json_string_arguments(self):
		self.ask(context='{"surface": "lms", "path": "/lms"}', scope='{"all": true}', history="[]")
		(_, kwargs) = self.enqueued[-1]
		self.assertEqual(kwargs["scope"], {"all": True})
		self.assertEqual(kwargs["context"]["path"], "/lms")

	def test_get_context_and_scopes(self):
		self.assertEqual(chat.get_context({"surface": "lms", "path": f"/lms/courses/{self.course}"})["context"]["scope_name"], self.course)
		self.assertIsNone(chat.get_context({"path": "/lms/courses/none"})["context"])
		self.assertIsNone(chat.get_context("not json")["context"])
		self.assertIn(self.course, [s["scope_name"] for s in chat.search_scopes("Api")["scopes"]])

	def test_hourly_limit(self):
		from gdn_rag.rag.config import get_settings

		settings = get_settings()
		settings["chat"]["hourly_limit_per_user"] = 2
		with mock.patch("gdn_rag.api.chat.get_settings", return_value=settings):
			answer.cache_drop(answer.inflight_key(self.member))
			key = frappe.cache.make_key(f"gdn_rag:rate:{self.member}:{int(__import__('time').time() // 3600)}")
			frappe.cache.delete(key)
			for _ in range(2):
				self.ask()
				self.run_job()
			with self.assertRaises(frappe.ValidationError):
				self.ask()
			frappe.cache.delete(key)
