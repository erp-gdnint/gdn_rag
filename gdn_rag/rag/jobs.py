"""Scheduler entry points."""

from __future__ import annotations

import frappe

from gdn_rag.rag import db, registry
from gdn_rag.rag.config import get_config, get_settings
from gdn_rag.rag.indexer import _add, _index_one
from gdn_rag.rag.log import get_logger


def _logger():
	return get_logger()


def scheduled_sync():
	"""Cron (every 15 minutes): queue the real job on the "long" queue (only one at a time)."""
	try:
		if not get_settings().indexing.scheduled or not db.tables_exist():
			return
		frappe.enqueue(
			"gdn_rag.rag.jobs.run_sync",
			queue="long",
			timeout=1500,
			job_id="gdn_rag:sync",
			deduplicate=True,
		)
	except Exception:
		_logger().exception("gdn_rag: scheduled_sync failed")


def run_sync() -> dict:
	"""Index documents changed since the last run. The watermark is saved in __gdn_rag_state."""
	settings = get_settings()
	limit = int(settings.indexing.max_docs_per_run)
	search = settings.search
	totals = {"refs": 0, "added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
	conf_by_key = {c["key"]: c for c in get_config().sources() if c.get("key")}
	for key, src in registry.get_sources().items():
		if conf_by_key.get(key, {}).get("index_mode", "both") not in ("scheduled", "both"):
			continue
		state_key = f"sync:{key}"
		run_started = frappe.utils.now()
		since = db.state_get(state_key)
		processed = 0
		last_modified = None
		truncated = False
		for ref in src.iter_refs(since):
			if processed >= limit:
				truncated = True
				break
			result = _index_one(src, ref, search)
			_add(totals, result)
			if ref.cascade:
				if result is None:
					src.on_ref_removed(ref)
				else:
					kept = src.dependent_refs(ref)
					for dep in kept:
						_add(totals, _index_one(src, dep, search))
					src.prune(ref, kept)
			processed += 1
			last_modified = ref.modified or last_modified
			if processed % 20 == 0:
				frappe.db.commit()
		totals["refs"] += processed
		# If we stopped early, continue from the last processed time next run (">=" + hash check
		# makes repeated work cheap). Otherwise move to the time this run started.
		db.state_set(state_key, last_modified if truncated and last_modified else run_started)
		frappe.db.commit()
	return totals


def cleanup_orphans():
	"""Daily: delete chunks of documents that no longer exist, and of sources that are off."""
	try:
		if not db.tables_exist():
			return
		available = registry.get_sources()
		for source in db.distinct_sources():
			if source not in available:
				db.delete_source(source)
		by_doctype: dict[tuple[str, str], list[str]] = {}
		for source, doctype, name in db.distinct_refs():
			if source in available:
				by_doctype.setdefault((source, doctype), []).append(name)
		from gdn_rag.rag.types import DocRef

		for (source, doctype), names in by_doctype.items():
			for i in range(0, len(names), 500):
				batch = names[i : i + 500]
				present = set(frappe.get_all(doctype, filters={"name": ["in", batch]}, pluck="name"))
				for name in batch:
					if name not in present:
						db.delete_ref(DocRef(source, doctype, name))
		frappe.db.commit()
	except Exception:
		_logger().exception("gdn_rag: cleanup_orphans failed")


def optimize_index():
	"""Weekly: merge FULLTEXT index changes."""
	try:
		if db.tables_exist():
			frappe.db.sql_ddl(f"OPTIMIZE TABLE {db.CHUNK}")
	except Exception:
		_logger().exception("gdn_rag: optimize_index failed")
