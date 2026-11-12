"""Document event handlers. They must be very cheap and must never raise."""

from __future__ import annotations

import frappe

from gdn_rag.rag import registry
from gdn_rag.rag.config import get_config, get_settings
from gdn_rag.rag.indexer import enqueue_ref
from gdn_rag.rag.log import get_logger
from gdn_rag.rag.types import DocRef


def _skip() -> bool:
	flags = frappe.flags
	return bool(flags.in_install or flags.in_migrate or flags.in_patch or flags.in_import or flags.in_test)


def _handle(doc, event: str, old_name: str | None = None):
	if _skip():
		return
	keys = registry.watched_doctypes().get(doc.doctype)
	if not keys:
		return
	if not get_settings().indexing.realtime:
		return
	sources = registry.get_sources()
	for key in keys:
		source = sources.get(key)
		if not source:
			continue
		conf = next((c for c in get_config().sources() if c.get("key") == key), {})
		if conf.get("index_mode", "both") not in ("realtime", "both"):
			continue
		fn = source.watched_doctypes().get(doc.doctype)
		if not fn:
			continue
		refs = list(fn(doc, event))
		if old_name:
			# a rename: also remove what was stored under the old name
			refs.append(DocRef(key, doc.doctype, old_name, cascade=doc.doctype == "LMS Course"))
		for ref in refs:
			enqueue_ref(ref)


def _run(doc, event, old_name=None):
	try:
		_handle(doc, event, old_name)
	except Exception:
		get_logger().exception("gdn_rag: event handler failed")


def on_update(doc, method=None, *args, **kwargs):
	_run(doc, "update")


def on_trash(doc, method=None, *args, **kwargs):
	# The job runs after the delete is committed, finds no document and removes its chunks.
	_run(doc, "trash")


def after_rename(doc, method=None, old=None, new=None, merge=False, *args, **kwargs):
	_run(doc, "rename", old_name=old)
