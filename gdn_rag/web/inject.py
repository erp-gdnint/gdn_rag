"""Add the chat widget to the LMS pages without touching the LMS app.

The LMS is a standalone Vue page (lms/www/lms.html) and ignores `web_include_js`.
So we add one <script> tag to its HTML in an `after_request` hook.
The hook only touches: GET, status 200, text/html, path /lms or /lms/..., logged-in allowed user.
"""

from __future__ import annotations

import html
import json
import os

import frappe

from gdn_rag.rag import registry
from gdn_rag.rag.access import user_allowed
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.log import get_logger

MARKER = "data-gdn-rag"
SCRIPT_PATH = "/assets/gdn_rag/js/gdn_rag_widget.js"
WIDGET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "js", "gdn_rag_widget.js")


def asset_version() -> str:
	"""Changes whenever the widget file changes. Browsers keep static assets for 12 hours, so the
	script URL needs this to pick up a new release at once."""
	try:
		return str(int(os.path.getmtime(WIDGET_FILE)))
	except OSError:
		return "1"


def client_settings(user: str, surface: str, settings) -> dict:
	"""The only settings the browser gets. Nothing secret."""
	return {
		"enabled": True,
		"surface": surface,
		"user": user,
		"shortcut": dict(settings.shortcut),
		"show_launcher": bool(settings.show_launcher),
		"history_turns": int(settings.chat.history_turns),
		"max_question_chars": int(settings.chat.max_question_chars),
		"asset_version": asset_version(),
	}


def is_lms_path(path: str) -> bool:
	return path == "/lms" or path.startswith("/lms/")


def should_inject(request, response) -> bool:
	if request.method != "GET" or response.status_code != 200:
		return False
	if response.mimetype != "text/html" or getattr(response, "direct_passthrough", False):
		return False
	return is_lms_path(request.path or "")


def after_request(response=None, request=None, **kwargs):
	"""Never raises. On any problem the page is returned unchanged."""
	try:
		if response is None or request is None or not should_inject(request, response):
			return
		user = frappe.session.user
		if user == "Guest":
			return
		settings = get_settings()
		if not (settings.enabled and settings.surfaces.lms) or not user_allowed(user, settings):
			return
		if "lms" not in registry.available_source_keys():
			return
		body = response.get_data(as_text=True)
		if MARKER in body:
			return
		index = body.rfind("</body>")
		if index == -1:
			return
		payload = html.escape(json.dumps(client_settings(user, "lms", settings)), quote=True)
		tag = f'<script src="{SCRIPT_PATH}?v={asset_version()}" defer {MARKER}="{payload}" data-surface="lms"></script>'
		response.set_data(body[:index] + tag + body[index:])
	except Exception:
		get_logger().exception("gdn_rag: could not inject the widget")

