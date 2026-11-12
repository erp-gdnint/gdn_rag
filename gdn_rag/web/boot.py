"""Give the widget its settings on Desk pages. Nothing secret goes into boot info."""

import frappe

from gdn_rag.rag import registry
from gdn_rag.rag.access import user_allowed
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.log import get_logger
from gdn_rag.web.inject import client_settings


def extend_bootinfo(bootinfo):
	try:
		user = frappe.session.user
		if user == "Guest":
			return
		settings = get_settings()
		if not (settings.enabled and settings.surfaces.desk) or not user_allowed(user, settings):
			return
		if not registry.available_source_keys():
			return
		bootinfo.gdn_rag = client_settings(user, "desk", settings)
	except Exception:
		get_logger().exception("gdn_rag: extend_bootinfo failed")
