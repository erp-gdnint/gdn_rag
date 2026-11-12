"""Loggers for GDN RAG.

Frappe sets every logger to ERROR on a production server (WARNING on a dev server). That would hide
our provider warnings, and the usage log (INFO) would never be written. So our own loggers get an
explicit level here. If Frappe is configured to be more verbose (DEBUG), that is kept.
"""

import logging

import frappe


def get_logger(name: str = "gdn_rag", level: int = logging.WARNING, file_count: int = 5) -> logging.Logger:
	logger = frappe.logger(name, allow_site=True, file_count=file_count)
	if logger.level == logging.NOTSET or logger.level > level:
		logger.setLevel(level)
	return logger
