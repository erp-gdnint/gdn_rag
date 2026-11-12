import json
import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from gdn_rag.rag import usage_log
from gdn_rag.rag.log import get_logger


class TestLogLevels(unittest.TestCase):
	def test_production_error_level_is_lowered_to_warning(self):
		# Frappe sets loggers to ERROR on production. Our provider warnings must still be written.
		frappe_logger = logging.getLogger("test-gdn-prod")
		frappe_logger.setLevel(logging.ERROR)
		with mock.patch("gdn_rag.rag.log.frappe.logger", return_value=frappe_logger):
			logger = get_logger()
		self.assertTrue(logger.isEnabledFor(logging.WARNING))
		self.assertFalse(logger.isEnabledFor(logging.INFO))

	def test_usage_logger_writes_info(self):
		frappe_logger = logging.getLogger("test-gdn-usage")
		frappe_logger.setLevel(logging.ERROR)
		with mock.patch("gdn_rag.rag.log.frappe.logger", return_value=frappe_logger):
			self.assertTrue(get_logger("gdn_rag_usage", logging.INFO).isEnabledFor(logging.INFO))

	def test_a_more_verbose_frappe_level_is_kept(self):
		frappe_logger = logging.getLogger("test-gdn-debug")
		frappe_logger.setLevel(logging.DEBUG)
		with mock.patch("gdn_rag.rag.log.frappe.logger", return_value=frappe_logger):
			self.assertEqual(get_logger().level, logging.DEBUG)

	def test_usage_line_has_no_question_text_by_default(self):
		lines = []
		fake = SimpleNamespace(info=lines.append)
		settings = SimpleNamespace(logging=SimpleNamespace(log_questions=False))
		with mock.patch.object(usage_log, "get_logger", return_value=fake), mock.patch.object(
			usage_log.frappe.utils, "now", return_value="2026-01-01 00:00:00"
		):
			usage_log.log_usage(settings, user="a@x.com", outcome="answered", question="my secret question")
		record = json.loads(lines[0])
		self.assertEqual(record["user"], "a@x.com")
		self.assertNotIn("question", record)
		self.assertNotIn("secret", lines[0])

	def test_question_text_is_logged_only_when_enabled(self):
		lines = []
		fake = SimpleNamespace(info=lines.append)
		settings = SimpleNamespace(logging=SimpleNamespace(log_questions=True))
		with mock.patch.object(usage_log, "get_logger", return_value=fake), mock.patch.object(
			usage_log.frappe.utils, "now", return_value="x"
		):
			usage_log.log_usage(settings, user="a@x.com", question="hello")
		self.assertEqual(json.loads(lines[0])["question"], "hello")


if __name__ == "__main__":
	unittest.main()
