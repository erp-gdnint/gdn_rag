"""Admin endpoints (System Manager only)."""

from __future__ import annotations

import frappe

from gdn_rag.rag import db, registry
from gdn_rag.rag.config import get_config, provider_ready


@frappe.whitelist(methods=["GET"])
def status():
	frappe.only_for("System Manager")
	providers = get_config().chat_providers()
	return {
		"mariadb": db.db_version(),
		"tables": db.tables_exist(),
		"stopword_probe": db.state_get("stopword_probe"),
		"sources": sorted(registry.get_sources()),
		"stats": db.stats() if db.tables_exist() else None,
		"providers": [
			{"name": p["name"], "priority": p.get("priority"), "ready": provider_ready(p)} for p in providers
		],
	}


@frappe.whitelist(methods=["POST"])
def reindex(source=None):
	"""Queue a full re-index on the long queue."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"gdn_rag.rag.indexer.reindex_all",
		queue="long",
		timeout=3600,
		job_id="gdn_rag:reindex_all",
		deduplicate=True,
		source_key=source,
	)
	return {"queued": True}
