"""HTTP API used by the chat widget. No endpoint allows guests."""

from __future__ import annotations

import json
import re
import time
from uuid import uuid4

import frappe
from frappe import _

from gdn_rag.rag import answer, registry
from gdn_rag.rag.access import user_allowed
from gdn_rag.rag.config import get_config, get_settings

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
MAX_HISTORY_ITEM_CHARS = 2000


def _forbid(message: str):
	frappe.throw(message, frappe.PermissionError)


def _guard():
	"""Every endpoint starts here: logged in, feature on, a source available, role allowed."""
	user = frappe.session.user
	if user == "Guest":
		_forbid(_("Please log in to use the assistant."))
	settings = get_settings()
	if not settings.enabled:
		_forbid(_("The assistant is turned off."))
	if not registry.available_source_keys():
		_forbid(_("The assistant has no content to search."))
	if not user_allowed(user, settings):
		_forbid(_("You do not have access to the assistant."))
	return user, settings


def _parse(value, default=None):
	"""Arguments can arrive as JSON strings or as already-parsed values."""
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return default
		try:
			return json.loads(value)
		except ValueError:
			return default
	return default if value is None else value


def _clean_history(history, turns: int) -> list[dict]:
	out = []
	for item in history if isinstance(history, list) else []:
		if not isinstance(item, dict):
			continue
		role, content = item.get("role"), item.get("content")
		if role in ("user", "assistant") and isinstance(content, str) and content.strip():
			out.append({"role": role, "content": content.strip()[:MAX_HISTORY_ITEM_CHARS]})
	return out[-2 * turns :] if turns > 0 else []


def _clean_scope(scope):
	scope = _parse(scope)
	if not isinstance(scope, dict):
		return None
	if scope.get("all"):
		return {"all": True}
	name, source = scope.get("scope_name"), scope.get("source")
	if isinstance(name, str) and name and isinstance(source, str) and source:
		return {
			"source": source,
			"scope_doctype": str(scope.get("scope_doctype") or ""),
			"scope_name": name,
		}
	return None


def _clean_context(context):
	context = _parse(context)
	if not isinstance(context, dict):
		return None
	route = context.get("desk_route")
	return {
		"surface": str(context.get("surface") or "")[:20],
		"path": str(context.get("path") or "")[:500],
		"desk_route": [str(x)[:140] for x in route[:6]] if isinstance(route, list) else None,
	}


def _check_rate_limit(user: str, limit: int):
	if limit <= 0:
		return
	key = frappe.cache.make_key(f"gdn_rag:rate:{user}:{int(time.time() // 3600)}")
	count = frappe.cache.incr(key)
	if count == 1:
		frappe.cache.expire(key, 3700)
	if count > limit:
		frappe.throw(_("You asked too many questions this hour. Please try again later."), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def ask(question=None, context=None, scope=None, history=None):
	"""Start an answer. Returns {"token": ...}. The widget then calls `poll`."""
	user, settings = _guard()
	chat = settings.chat

	question = (question or "").strip() if isinstance(question, str) else ""
	if not question:
		frappe.throw(_("Please type a question."), frappe.ValidationError)
	if len(question) > int(chat.max_question_chars):
		frappe.throw(_("The question is too long."), frappe.ValidationError)
	if not get_config().chat_providers():
		frappe.throw(answer.NOT_CONFIGURED, frappe.ValidationError)

	context = _clean_context(context)
	scope = _clean_scope(scope)
	history = _clean_history(_parse(history, []), int(chat.history_turns))

	token = uuid4().hex
	lock = frappe.cache.make_key(answer.inflight_key(user))
	if not frappe.cache.set(lock, token, nx=True, ex=int(chat.job_timeout)):
		frappe.throw(_("Please wait for the current answer to finish."), frappe.ValidationError)
	try:
		_check_rate_limit(user, int(chat.hourly_limit_per_user))
		state = answer.AnswerState(token, user, int(chat.answer_ttl_seconds))
		state.write()
		frappe.enqueue(
			"gdn_rag.rag.answer.run",
			queue=chat.queue,
			timeout=int(chat.job_timeout),
			token=token,
			user=user,
			question=question,
			context=context,
			scope=scope,
			history=history,
			surface=(context or {}).get("surface"),
		)
	except Exception:
		answer.cache_drop(answer.inflight_key(user))
		raise
	return {"token": token}


def _load_answer(token):
	if not isinstance(token, str) or not _TOKEN.match(token):
		return None
	data = answer.cache_fetch(answer.answer_key(token))
	if not data or data.get("user") != frappe.session.user:
		return None  # missing, expired, or somebody else's token: look the same
	return data


@frappe.whitelist(methods=["GET"])
def poll(token=None):
	_guard()
	data = _load_answer(token)
	if not data:
		return {"status": "expired"}
	return {k: data.get(k) for k in ("status", "text", "citations", "error", "provider")}


@frappe.whitelist(methods=["POST"])
def cancel(token=None):
	_guard()
	if _load_answer(token):
		answer.cache_put(answer.cancel_key(token), 1, 600)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def get_context(context=None):
	"""What page is the user on? Returns {"context": {label, source, scope_doctype, scope_name} | None}."""
	user, _settings = _guard()
	page = answer.resolve_context(user, _clean_context(context))
	if not page:
		return {"context": None}
	return {
		"context": {
			"label": page.label,
			"source": page.source,
			"scope_doctype": page.scope_doctype,
			"scope_name": page.scope_name,
		}
	}


@frappe.whitelist(methods=["GET"])
def search_scopes(txt=""):
	"""Courses (or other scopes) the user may pick in the widget."""
	user, _settings = _guard()
	txt = txt if isinstance(txt, str) else ""
	results: list[dict] = []
	for source in registry.get_sources().values():
		results.extend(source.search_scopes(user, txt, 20))
	return {"scopes": results[:20]}
