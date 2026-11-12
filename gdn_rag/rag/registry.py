"""Finds the content sources registered by apps (hook `gdn_rag_sources`)."""

from __future__ import annotations

import frappe

from gdn_rag.rag.config import get_config
from gdn_rag.rag.log import get_logger

_AVAILABLE_KEY = "gdn_rag:available_sources"
_WATCHED_KEY = "gdn_rag:watched_doctypes"
_CACHE_SECONDS = 300


def _logger():
	return get_logger()


def _load_classes() -> list[type]:
	classes = []
	for path in frappe.get_hooks("gdn_rag_sources") or []:
		try:
			classes.append(frappe.get_attr(path))
		except Exception:
			_logger().exception("gdn_rag: cannot import source %s", path)
	return classes


def get_sources() -> dict:
	"""Enabled and available sources: {key: instance}. Memoized for the current request/job."""
	cached = getattr(frappe.local, "gdn_rag_sources", None)
	if cached is not None:
		return cached
	conf_by_key = {c["key"]: c for c in get_config().sources() if c.get("key")}
	sources: dict = {}
	for cls in _load_classes():
		conf = conf_by_key.get(getattr(cls, "key", ""))
		if not conf or not conf.get("enabled", True):
			continue
		try:
			instance = cls(conf)
			if instance.is_available():
				sources[instance.key] = instance
		except Exception:
			_logger().exception("gdn_rag: source %s failed its availability check", cls)
	frappe.local.gdn_rag_sources = sources
	return sources


def get_source(key: str):
	return get_sources().get(key)


def available_source_keys() -> list[str]:
	"""Cheap check used on every page: cached in Redis for 5 minutes."""
	try:
		keys = frappe.cache.get_value(_AVAILABLE_KEY)
	except Exception:
		keys = None
	if keys is None:
		keys = sorted(get_sources())
		try:
			frappe.cache.set_value(_AVAILABLE_KEY, keys, expires_in_sec=_CACHE_SECONDS)
		except Exception:
			pass
	return keys


def watched_doctypes() -> dict[str, list[str]]:
	"""{doctype: [source keys]} for the document events. Cached in Redis for 5 minutes."""
	try:
		cached = frappe.cache.get_value(_WATCHED_KEY)
	except Exception:
		cached = None
	if cached is not None:
		return cached
	result: dict[str, list[str]] = {}
	for key, source in get_sources().items():
		for doctype in source.watched_doctypes():
			result.setdefault(doctype, []).append(key)
	try:
		frappe.cache.set_value(_WATCHED_KEY, result, expires_in_sec=_CACHE_SECONDS)
	except Exception:
		pass
	return result


def clear_cache():
	frappe.local.gdn_rag_sources = None
	try:
		frappe.cache.delete_value([_AVAILABLE_KEY, _WATCHED_KEY])
	except Exception:
		pass
