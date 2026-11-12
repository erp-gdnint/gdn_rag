"""Index one document (and optionally the documents that depend on it)."""

from __future__ import annotations

import frappe

from gdn_rag.rag import db, registry
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.types import DocRef


def _index_one(source, ref: DocRef, search: dict, force: bool = False) -> dict | None:
	"""Returns the stats of replace_chunks, or None if the document was removed."""
	chunks = source.build_chunks(ref)
	if chunks is None:
		db.delete_ref(ref)
		return None
	return db.replace_chunks(ref, chunks, search, force=force)


def index_ref(source: str, doctype: str, name: str, cascade: bool = False, force: bool = False):
	"""Background job (queue "long"): build and store the chunks of one document."""
	src = registry.get_source(source)
	if not src:
		return
	search = get_settings().search
	ref = DocRef(source, doctype, name, cascade=cascade)
	result = _index_one(src, ref, search, force)
	if cascade:
		if result is None:
			src.on_ref_removed(ref)
		else:
			kept = src.dependent_refs(ref)
			for dep in kept:
				_index_one(src, dep, search, force)
			src.prune(ref, kept)


def _job_status(job_id: str) -> str | None:
	"""Status of a Frappe/RQ job ("queued", "started", ...) or None. Never raises."""
	try:
		from frappe.utils.background_jobs import get_job

		job = get_job(job_id)
		if not job:
			return None
		status = job.get_status()
		return str(getattr(status, "value", status))  # JobStatus is an enum: use its plain value
	except Exception:
		return None


def enqueue_ref(ref: DocRef, force: bool = False):
	"""Queue a re-index job for one document, without queueing the same document twice.

	- A job for this document is already WAITING: do nothing. It will read the newest data when it runs.
	- A job for this document is RUNNING right now: it may have read old data, so queue one more
	  (under a second job id, so Frappe does not skip it).
	We check this ourselves because Frappe writes an ERROR line to frappe.log for every skipped job, and
	editing a course saves several linked documents in a row.
	"""
	suffix = ":cascade" if ref.cascade else ""
	job_id = f"gdn_rag:{ref.source}:{ref.doctype}:{ref.name}{suffix}"
	# jobs are really queued only after the transaction commits, so Redis cannot see two saves of the
	# same document in one request. Remember what this request already scheduled.
	scheduled = getattr(frappe.local, "gdn_rag_scheduled", None)
	if scheduled is None:
		scheduled = frappe.local.gdn_rag_scheduled = set()
	if job_id in scheduled:
		return
	scheduled.add(job_id)
	status = _job_status(job_id)
	if status == "queued":
		return
	if status == "started":
		job_id += ":again"
		if _job_status(job_id) in ("queued", "started"):
			return
	frappe.enqueue(
		"gdn_rag.rag.indexer.index_ref",
		queue="long",
		timeout=900,
		job_id=job_id,
		deduplicate=True,  # still on, for a race between two web workers
		enqueue_after_commit=True,
		source=ref.source,
		doctype=ref.doctype,
		name=ref.name,
		cascade=ref.cascade,
		force=force,
	)


def reindex_all(source_key: str | None = None, force: bool = True, progress=None) -> dict:
	"""Index everything now, in the current process (used by the CLI). Commits as it goes."""
	search = get_settings().search
	totals = {"refs": 0, "added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
	for key, src in registry.get_sources().items():
		if source_key and key != source_key:
			continue
		for ref in src.iter_refs(None):
			result = _index_one(src, ref, search, force)
			_add(totals, result)
			if ref.cascade:
				if result is None:
					src.on_ref_removed(ref)
				else:
					kept = src.dependent_refs(ref)
					for dep in kept:
						_add(totals, _index_one(src, dep, search, force))
					src.prune(ref, kept)
			totals["refs"] += 1
			if progress:
				progress(ref)
			frappe.db.commit()
	return totals


def reindex_one(source_key: str, doctype: str, name: str, force: bool = True) -> dict:
	totals = {"refs": 0, "added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
	src = registry.get_source(source_key)
	if not src:
		return totals
	search = get_settings().search
	ref = DocRef(source_key, doctype, name, cascade=doctype == "LMS Course")
	result = _index_one(src, ref, search, force)
	_add(totals, result)
	totals["refs"] += 1
	if ref.cascade and result is not None:
		kept = src.dependent_refs(ref)
		for dep in kept:
			_add(totals, _index_one(src, dep, search, force))
		src.prune(ref, kept)
	frappe.db.commit()
	return totals


def _add(totals: dict, result: dict | None):
	if result:
		for k in ("added", "updated", "deleted", "unchanged"):
			totals[k] += result.get(k, 0)
