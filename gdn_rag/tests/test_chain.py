import unittest
from unittest import mock

from gdn_rag.rag.llm import chain
from gdn_rag.rag.llm.base import (
	AllProvidersFailed,
	ChatAdapter,
	NotConfigured,
	ProviderAuthError,
	ProviderUnavailable,
	scrub,
)
from gdn_rag.rag.llm.anthropic import split_messages


class MemoryBreaker(chain.Breaker):
	"""Same logic as the Redis breaker, but in memory."""

	def __init__(self):
		self.down, self.backoff, self.auth_logged = {}, {}, set()

	def is_down(self, name):
		return name in self.down

	def mark_down(self, name, auth=False):
		previous = self.backoff.get(name, 0)
		seconds = chain.AUTH_BACKOFF if auth else min(chain.BACKOFF_MAX, previous * 2 if previous else chain.BACKOFF_START)
		self.down[name] = seconds
		self.backoff[name] = seconds
		return seconds

	def mark_ok(self, name):
		self.down.pop(name, None)
		self.backoff.pop(name, None)

	def should_log_auth(self, name):
		if name in self.auth_logged:
			return False
		self.auth_logged.add(name)
		return True


class FakeAdapter(ChatAdapter):
	"""script: list of pieces, or an exception to raise. An exception after pieces = fail mid-stream."""

	calls = []

	def __init__(self, conf):
		super().__init__(conf, None)
		self.script = conf["script"]

	def stream(self, messages):
		FakeAdapter.calls.append(self.name)
		for item in self.script:
			if isinstance(item, Exception):
				raise item
			yield item


def provider(name, script, priority=10):
	return {"name": name, "priority": priority, "api_key": None, "script": script}


def collect(gen):
	return list(gen)


class TestChain(unittest.TestCase):
	def setUp(self):
		FakeAdapter.calls = []
		self.breaker = MemoryBreaker()
		patcher = mock.patch.object(chain, "_logger", return_value=mock.Mock())
		patcher.start()
		self.addCleanup(patcher.stop)
		log_patcher = mock.patch.object(chain.frappe, "log_error")
		self.log_error = log_patcher.start()
		self.addCleanup(log_patcher.stop)

	def run_chain(self, providers):
		return collect(chain.stream_chat([{"role": "user", "content": "hi"}], providers, self.breaker, FakeAdapter))

	def test_first_provider_answers(self):
		out = self.run_chain([provider("local", ["Hel", "lo"]), provider("gemini", ["no"])])
		self.assertEqual(out, [("local", "Hel"), ("local", "lo")])
		self.assertEqual(FakeAdapter.calls, ["local"])

	def test_fallback_before_first_token(self):
		out = self.run_chain([provider("local", [ProviderUnavailable("down")]), provider("gemini", ["ok"])])
		self.assertEqual(out, [("gemini", "ok")])
		self.assertTrue(self.breaker.is_down("local"))

	def test_second_question_skips_the_down_provider(self):
		providers = [provider("local", [ProviderUnavailable("down")]), provider("gemini", ["ok"])]
		self.run_chain(providers)
		FakeAdapter.calls = []
		self.run_chain(providers)
		self.assertEqual(FakeAdapter.calls, ["gemini"])  # local was not tried again

	def test_no_fallback_in_the_middle_of_an_answer(self):
		out = self.run_chain([provider("local", ["Part", ProviderUnavailable("cut")]), provider("gemini", ["other"])])
		self.assertEqual(out[0], ("local", "Part"))
		self.assertEqual(out[-1], ("local", chain.INTERRUPTED))
		self.assertNotIn("gemini", FakeAdapter.calls)

	def test_empty_answer_counts_as_failure(self):
		out = self.run_chain([provider("local", []), provider("gemini", ["ok"])])
		self.assertEqual(out, [("gemini", "ok")])

	def test_auth_error_uses_long_cooldown_and_logs_once(self):
		providers = [provider("claude", [ProviderAuthError("401")]), provider("gemini", ["ok"])]
		self.run_chain(providers)
		self.assertEqual(self.breaker.down["claude"], chain.AUTH_BACKOFF)
		self.assertEqual(self.log_error.call_count, 1)
		self.breaker.down.clear()  # cooldown over, fails again: no second log line
		self.run_chain(providers)
		self.assertEqual(self.log_error.call_count, 1)

	def test_backoff_doubles_up_to_the_maximum(self):
		providers = [provider("local", [ProviderUnavailable("down")]), provider("gemini", ["ok"])]
		seen = []
		for _ in range(6):
			self.breaker.down.clear()
			self.run_chain(providers)
			seen.append(self.breaker.down["local"])
		self.assertEqual(seen, [60, 120, 240, 480, 900, 900])

	def test_success_clears_the_breaker(self):
		self.breaker.mark_down("local")
		self.breaker.down.clear()  # cooldown ended, backoff memory stays
		self.run_chain([provider("local", ["ok"])])
		self.assertEqual(self.breaker.backoff, {})

	def test_all_providers_failing(self):
		with self.assertRaises(AllProvidersFailed):
			self.run_chain([provider("a", [ProviderUnavailable("x")]), provider("b", [ProviderUnavailable("y")])])

	def test_all_down_still_tries_in_priority_order(self):
		self.breaker.mark_down("a")
		self.breaker.mark_down("b")
		out = self.run_chain([provider("a", ["from a"]), provider("b", ["from b"])])
		self.assertEqual(out, [("a", "from a")])

	def test_unexpected_error_in_a_provider_falls_through(self):
		out = self.run_chain([provider("a", [KeyError("boom")]), provider("b", ["ok"])])
		self.assertEqual(out, [("b", "ok")])

	def test_no_provider_ready(self):
		with self.assertRaises(NotConfigured):
			self.run_chain([])
		with mock.patch.object(chain, "provider_ready", return_value=False):
			with self.assertRaises(NotConfigured):
				self.run_chain([provider("a", ["x"])])


class TestRealStreaming(unittest.TestCase):
	"""Talks to a real local HTTP server (HTTP/1.0, no chunked encoding, the hardest case)."""

	def serve(self, handler_body):
		import threading
		from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

		class Handler(BaseHTTPRequestHandler):
			def log_message(self, *a):
				pass

			def do_POST(self):
				self.rfile.read(int(self.headers["Content-Length"]))
				handler_body(self)

		server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		threading.Thread(target=server.serve_forever, daemon=True).start()
		self.addCleanup(server.server_close)
		self.addCleanup(server.shutdown)
		return f"http://127.0.0.1:{server.server_address[1]}/v1"

	def test_pieces_arrive_while_the_server_is_still_sending(self):
		import json
		import time

		def body(h):
			h.send_response(200)
			h.send_header("Content-Type", "text/event-stream")
			h.end_headers()
			for piece in ("one ", "two ", "three"):
				h.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n").encode())
				h.wfile.flush()
				time.sleep(0.5)
			h.wfile.write(b"data: [DONE]\n\n")

		from gdn_rag.rag.llm.openai_compatible import OpenAICompatibleAdapter

		adapter = OpenAICompatibleAdapter({"name": "t", "base_url": self.serve(body), "model": "m"}, None)
		started = time.monotonic()
		arrivals = [(piece, time.monotonic() - started) for piece in adapter.stream([{"role": "user", "content": "hi"}])]
		self.assertEqual([p for p, _ in arrivals], ["one ", "two ", "three"])
		self.assertLess(arrivals[0][1], 0.4, "the first piece must not wait for the end of the answer")
		self.assertGreater(arrivals[2][1] - arrivals[0][1], 0.8)

	def test_http_errors_map_to_the_right_exception(self):
		from gdn_rag.rag.llm.openai_compatible import OpenAICompatibleAdapter

		for status, expected in ((401, ProviderAuthError), (403, ProviderAuthError), (429, ProviderUnavailable), (500, ProviderUnavailable), (404, ProviderUnavailable)):

			def body(h, status=status):
				h.send_response(status)
				h.end_headers()
				h.wfile.write(b'{"error": "Bearer sk-abcdefghijklmnopqrstuvwxyz leaked?"}')

			adapter = OpenAICompatibleAdapter({"name": "t", "base_url": self.serve(body), "model": "m"}, "sk-abcdefghijklmnopqrstuvwxyz")
			with self.assertRaises(expected) as ctx:
				list(adapter.stream([{"role": "user", "content": "hi"}]))
			self.assertNotIn("abcdefghijklmnop", str(ctx.exception))

	def test_unreachable_server(self):
		from gdn_rag.rag.llm.openai_compatible import OpenAICompatibleAdapter

		adapter = OpenAICompatibleAdapter({"name": "t", "base_url": "http://127.0.0.1:1/v1", "model": "m", "timeout": {"connect": 1, "read": 1}}, None)
		with self.assertRaises(ProviderUnavailable):
			list(adapter.stream([{"role": "user", "content": "hi"}]))


class TestScrubAndAnthropic(unittest.TestCase):
	def test_scrub_removes_keys(self):
		text = "Authorization: Bearer sk-abcdefghijklmnop1234 and x-api-key: topsecret and ?key=AIzaSyABCDEFGHIJKLMNOP"
		clean = scrub(text, secret="topsecret")
		for secret in ("sk-abcdefghijklmnop1234", "topsecret", "AIzaSyABCDEFGHIJKLMNOP"):
			self.assertNotIn(secret, clean)

	def test_anthropic_message_shape(self):
		system, msgs = split_messages(
			[
				{"role": "system", "content": "SYS"},
				{"role": "assistant", "content": "orphan first"},
				{"role": "user", "content": "q1"},
				{"role": "user", "content": "q1b"},
				{"role": "assistant", "content": "a1"},
				{"role": "user", "content": "q2"},
			]
		)
		self.assertEqual(system, "SYS")
		self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
		self.assertEqual(msgs[0]["content"], "q1\n\nq1b")


if __name__ == "__main__":
	unittest.main()
