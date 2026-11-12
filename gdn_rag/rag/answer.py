"""The answer pipeline: context -> retrieve -> prompt -> LLM -> citations.

`pipeline()` is a generator of events and is used both by the background job (`run`)
and by the CLI (`bench gdn-rag ask`).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator

import frappe

from gdn_rag.rag import prompts, registry, retriever
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.llm import chain
from gdn_rag.rag.llm.base import AllProvidersFailed, NotConfigured
from gdn_rag.rag.log import get_logger
from gdn_rag.rag.types import Hit, PageContext
from gdn_rag.rag.usage_log import log_usage

GENERIC_ERROR = "The assistant is temporarily unavailable. Please try again later."
NOT_CONFIGURED = "The assistant is not configured yet."
_CITE = re.compile(r"\[(\d{1,2})\]")


def _logger():
	return get_logger()


# ---------------------------------------------------------------- Redis helpers
# These talk to Redis directly (JSON values). We do NOT use frappe.cache.get_value() for
# answer state: it remembers values inside the process, so a flag set by another process
# (for example "cancel") would never be seen by a running job.


def cache_put(key: str, value, ttl: int):
	frappe.cache.set(frappe.cache.make_key(key), json.dumps(value), ex=int(ttl))


def cache_fetch(key: str):
	raw = frappe.cache.get(frappe.cache.make_key(key))
	if raw is None:
		return None
	try:
		return json.loads(raw)
	except ValueError:
		return None


def cache_drop(*keys: str):
	frappe.cache.delete(*[frappe.cache.make_key(k) for k in keys])


# ---------------------------------------------------------------- Redis state


def answer_key(token: str) -> str:
	return f"gdn_rag:answer:{token}"


def cancel_key(token: str) -> str:
	return f"gdn_rag:cancel:{token}"


def inflight_key(user: str) -> str:
	return f"gdn_rag:inflight:{user}"


class AnswerState:
	"""The answer that the widget polls. Lives in Redis for a short time."""

	def __init__(self, token: str, user: str, ttl: int):
		self.token, self.user, self.ttl = token, user, ttl
		self.data = {"user": user, "status": "queued", "text": "", "citations": [], "error": None, "provider": None}
		self._last_write = 0.0

	def write(self, force: bool = True, **changes):
		self.data.update(changes)
		now = time.monotonic()
		if force or now - self._last_write >= 0.25:
			cache_put(answer_key(self.token), self.data, self.ttl)
			self._last_write = now

	def cancelled(self) -> bool:
		return cache_fetch(cancel_key(self.token)) is not None


# ---------------------------------------------------------------- pipeline


def resolve_context(user: str, context: dict | None) -> PageContext | None:
	if not context:
		return None
	for source in registry.get_sources().values():
		try:
			found = source.resolve_context(context, user)
		except Exception:
			_logger().exception("gdn_rag: resolve_context failed")
			found = None
		if found:
			return found
	return None


def effective_scope(scope: dict | None, page: PageContext | None) -> dict | None:
	"""An explicit scope wins. {"all": true} means everything. Otherwise use the page's course."""
	if scope and scope.get("all"):
		return None
	if scope and scope.get("scope_name"):
		return {"source": scope.get("source"), "scope_doctype": scope.get("scope_doctype"), "scope_name": scope["scope_name"]}
	if page:
		return {"source": page.source, "scope_doctype": page.scope_doctype, "scope_name": page.scope_name}
	return None


def parse_citations(text: str, hits: list[Hit]) -> list[dict]:
	"""Map [n] markers to the sources. If the model cited nothing, return the top 3 as 'related'."""
	citations: list[dict] = []
	seen_routes: set[str] = set()
	for match in _CITE.finditer(text):
		n = int(match.group(1))
		if 1 <= n <= len(hits):
			hit = hits[n - 1]
			if hit.route not in seen_routes:
				seen_routes.add(hit.route)
				citations.append({"n": n, "title": hit.title, "route": hit.route})
	if citations:
		return citations
	related = []
	for i, hit in enumerate(hits[:3], start=1):
		if hit.route not in seen_routes:
			seen_routes.add(hit.route)
			related.append({"n": i, "title": hit.title, "route": hit.route, "related": True})
	return related


def pipeline(
	user: str,
	question: str,
	context: dict | None = None,
	scope: dict | None = None,
	history: list[dict] | None = None,
	settings=None,
) -> Iterator[dict]:
	"""Yields events: start, hits, delta, done. May raise AllProvidersFailed / NotConfigured."""
	settings = settings or get_settings()
	page = resolve_context(user, context)
	used_scope = effective_scope(scope, page)
	yield {"type": "start", "context": page.label if page else None, "scope": used_scope}

	hits, debug = retriever.retrieve(user, question, settings, used_scope, page, history)
	yield {"type": "hits", "hits": hits, "debug": debug}
	if not hits:
		yield {"type": "delta", "provider": None, "text": settings.chat.not_found_message}
		yield {"type": "done", "citations": [], "outcome": "not_found", "hits": 0}
		return

	messages = prompts.build_messages(
		question, hits, history, settings.chat.not_found_message, page.label if page else None,
		int(settings.chat.history_turns),
	)
	text = ""
	provider = None
	for provider, piece in chain.stream_chat(messages):
		text += piece
		yield {"type": "delta", "provider": provider, "text": piece}
	outcome = "not_found" if text.strip() == settings.chat.not_found_message else "answered"
	citations = [] if outcome == "not_found" else parse_citations(text, hits)
	yield {"type": "done", "citations": citations, "outcome": outcome, "hits": len(hits), "provider": provider}


# ---------------------------------------------------------------- background job


def run(token: str, user: str, question: str, context=None, scope=None, history=None, surface=None):
	"""Background job: runs the pipeline and writes progress to Redis for the widget to poll."""
	started = time.monotonic()
	settings = get_settings()
	state = AnswerState(token, user, int(settings.chat.answer_ttl_seconds))
	outcome, provider, hit_count = "error", None, 0
	try:
		if state.cancelled():
			state.write(status="cancelled")
			outcome = "cancelled"
			return
		frappe.set_user(user)
		state.write(status="running")
		text = ""
		for event in pipeline(user, question, context, scope, history, settings):
			kind = event["type"]
			if kind == "delta":
				text += event["text"]
				provider = event.get("provider") or provider
				state.write(force=False, status="streaming", text=text, provider=provider)
				if state.cancelled():
					state.write(status="cancelled", text=text)
					outcome = "cancelled"
					return
			elif kind == "hits":
				hit_count = len(event["hits"])
			elif kind == "done":
				outcome = event["outcome"]
				state.write(status="done", text=text, citations=event["citations"], provider=provider)
	except NotConfigured:
		state.write(status="error", error=NOT_CONFIGURED)
	except AllProvidersFailed:
		state.write(status="error", error=GENERIC_ERROR)
	except Exception:
		_logger().exception("gdn_rag: answer job failed")
		state.write(status="error", error=GENERIC_ERROR)
	finally:
		try:
			cache_drop(inflight_key(user))
		except Exception:
			pass
		log_usage(
			settings,
			user=user,
			surface=surface,
			scope=(scope or {}).get("scope_name") if isinstance(scope, dict) else None,
			hits=hit_count,
			provider=provider,
			latency_ms=int((time.monotonic() - started) * 1000),
			outcome=outcome,
			question_chars=len(question or ""),
			question=question,
		)
