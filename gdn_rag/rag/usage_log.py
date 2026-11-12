"""One JSON line per question in logs/gdn_rag_usage.log (no question text unless enabled)."""

from __future__ import annotations

import json
import logging

import frappe

from gdn_rag.rag.log import get_logger



def log_usage(settings, **fields):
	try:
		record = {"ts": frappe.utils.now(), **fields}
		if not settings.logging.log_questions:
			record.pop("question", None)
		logger = get_logger("gdn_rag_usage", logging.INFO, 10)
		logger.info(json.dumps(record, default=str, ensure_ascii=False))
	except Exception:
		pass
