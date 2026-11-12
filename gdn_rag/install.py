"""Install, migrate and uninstall hooks. They must never break a site."""

import frappe

from gdn_rag.rag.log import get_logger


def _safe_ensure_schema(label: str):
	from gdn_rag.rag import db, registry

	try:
		db.ensure_schema()
	except Exception:
		get_logger().exception("gdn_rag: %s could not create tables", label)
	try:
		registry.clear_cache()
	except Exception:
		pass


def after_install():
	_safe_ensure_schema("after_install")


def after_migrate():
	_safe_ensure_schema("after_migrate")


def before_uninstall():
	from gdn_rag.rag import db

	try:
		db.drop_schema()
	except Exception:
		get_logger().exception("gdn_rag: could not drop tables")
