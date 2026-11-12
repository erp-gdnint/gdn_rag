import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from werkzeug.wrappers import Response

from gdn_rag.web import inject

PAGE = "<html><body><div id='app'></div><script>boot()</script></body></html>"


def make_request(path="/lms/courses/x", method="GET"):
	return SimpleNamespace(path=path, method=method)


def make_response(body=PAGE, status=200, mimetype="text/html"):
	return Response(body, status=status, mimetype=mimetype)


class Settings(dict):
	__getattr__ = dict.get


SETTINGS = Settings(
	enabled=True,
	surfaces=Settings(lms=True, desk=True),
	allowed_roles=[],
	shortcut={"ctrl": True, "alt": True, "shift": False, "code": "KeyI"},
	show_launcher=True,
	chat=Settings(history_turns=4, max_question_chars=2000),
)


class TestShouldInject(unittest.TestCase):
	def test_paths(self):
		for path, expected in [
			("/lms", True), ("/lms/", True), ("/lms/courses/a/learn/1-2", True),
			("/lmsx", False), ("/app/lms-course", False), ("/api/method/x", False), ("/", False),
		]:  # fmt: skip
			self.assertEqual(inject.is_lms_path(path), expected, path)

	def test_only_get_200_html(self):
		self.assertTrue(inject.should_inject(make_request(), make_response()))
		self.assertFalse(inject.should_inject(make_request(method="POST"), make_response()))
		self.assertFalse(inject.should_inject(make_request(), make_response(status=404)))
		self.assertFalse(inject.should_inject(make_request(), make_response(mimetype="application/json")))
		self.assertFalse(inject.should_inject(make_request(path="/app/home"), make_response()))


class TestAfterRequest(unittest.TestCase):
	def setUp(self):
		self.patches = [
			mock.patch.object(inject.frappe, "session", SimpleNamespace(user="a@x.com")),
			mock.patch.object(inject, "get_settings", return_value=SETTINGS),
			mock.patch.object(inject, "user_allowed", return_value=True),
			mock.patch.object(inject.registry, "available_source_keys", return_value=["lms"]),
			mock.patch.object(inject.frappe, "logger", return_value=logging.getLogger("test-gdn-inject")),
		]
		for p in self.patches:
			p.start()
			self.addCleanup(p.stop)

	def run_hook(self, response=None, request=None):
		response = response or make_response()
		inject.after_request(response=response, request=request or make_request())
		return response.get_data(as_text=True)

	def test_script_is_added_before_the_last_body_tag(self):
		body = self.run_hook()
		self.assertIn("/assets/gdn_rag/js/gdn_rag_widget.js", body)
		self.assertLess(body.index("gdn_rag_widget.js"), body.rindex("</body>"))
		self.assertEqual(body.count("data-gdn-rag="), 1)

	def test_no_secrets_and_valid_settings_in_the_tag(self):
		body = self.run_hook()
		self.assertIn("&quot;user&quot;: &quot;a@x.com&quot;", body)
		for word in ("api_key", "providers", "secret"):
			self.assertNotIn(word, body)

	def test_script_url_is_versioned_by_the_file_time(self):
		# static files are cached for 12 hours, so a new release needs a new URL
		self.assertRegex(self.run_hook(), r'gdn_rag_widget\.js\?v=\d+"')

	def test_asset_version_follows_the_file(self):
		with mock.patch.object(inject.os.path, "getmtime", return_value=111):
			first = inject.asset_version()
		with mock.patch.object(inject.os.path, "getmtime", return_value=222):
			second = inject.asset_version()
		self.assertNotEqual(first, second)
		with mock.patch.object(inject.os.path, "getmtime", side_effect=OSError):
			self.assertEqual(inject.asset_version(), "1")  # a missing file must not break the page

	def test_settings_for_the_browser_carry_the_version(self):
		self.assertEqual(inject.client_settings("a@x.com", "desk", SETTINGS)["asset_version"], inject.asset_version())

	def test_not_added_twice(self):
		once = self.run_hook()
		twice = self.run_hook(make_response(once))
		self.assertEqual(twice.count("data-gdn-rag="), 1)

	def test_guest_gets_nothing(self):
		with mock.patch.object(inject.frappe, "session", SimpleNamespace(user="Guest")):
			self.assertEqual(self.run_hook(), PAGE)

	def test_other_pages_unchanged(self):
		self.assertEqual(self.run_hook(request=make_request("/app/home")), PAGE)
		self.assertEqual(self.run_hook(make_response(mimetype="application/json", body="{}")), "{}")

	def test_turned_off_unchanged(self):
		off = Settings(SETTINGS, enabled=False)
		with mock.patch.object(inject, "get_settings", return_value=off):
			self.assertEqual(self.run_hook(), PAGE)
		no_lms = Settings(SETTINGS, surfaces=Settings(lms=False, desk=True))
		with mock.patch.object(inject, "get_settings", return_value=no_lms):
			self.assertEqual(self.run_hook(), PAGE)

	def test_role_not_allowed_unchanged(self):
		with mock.patch.object(inject, "user_allowed", return_value=False):
			self.assertEqual(self.run_hook(), PAGE)

	def test_lms_not_installed_unchanged(self):
		with mock.patch.object(inject.registry, "available_source_keys", return_value=[]):
			self.assertEqual(self.run_hook(), PAGE)

	def test_page_without_body_tag_unchanged(self):
		self.assertEqual(self.run_hook(make_response("<p>no body tag</p>")), "<p>no body tag</p>")

	def test_errors_never_escape(self):
		with mock.patch.object(inject, "get_settings", side_effect=RuntimeError("boom")):
			self.assertEqual(self.run_hook(), PAGE)  # no exception, page unchanged


if __name__ == "__main__":
	unittest.main()
