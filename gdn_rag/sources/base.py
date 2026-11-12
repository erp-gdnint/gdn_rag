"""The plugin interface. The core talks to content only through this class.

To add a new source (CRM, other DocTypes ...): subclass BaseSource, set `key`, and add the
class path to the `gdn_rag_sources` hook of your app. See customisations/docs/adding-a-source.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import frappe

from gdn_rag.rag.types import AccessRule, ChunkInput, DocRef, Hit, PageContext


class BaseSource:
	key: str = ""  # unique, for example "lms"
	label: str = ""
	required_apps: tuple[str, ...] = ()  # apps that must be installed
	required_doctypes: tuple[str, ...] = ()  # doctypes that must exist

	def __init__(self, conf: dict | None = None):
		self.conf = conf or {}

	# ---- availability
	def is_available(self) -> bool:
		"""True when the needed apps are installed and the needed tables exist."""
		installed = set(frappe.get_installed_apps())
		if any(app not in installed for app in self.required_apps):
			return False
		return all(frappe.db.table_exists(dt) for dt in self.required_doctypes)

	# ---- indexing
	def watched_doctypes(self) -> dict[str, Callable[[object, str], list[DocRef]]]:
		"""doctype -> function(doc, event) that returns the refs to re-index.
		event is "update", "trash" or "rename"."""
		return {}

	def iter_refs(self, since: str | None) -> Iterator[DocRef]:
		"""Refs changed since `since` (a datetime string), or all refs when `since` is None.
		Each ref should carry `modified`; the scheduler sorts by it."""
		return iter(())

	def build_chunks(self, ref: DocRef) -> list[ChunkInput] | None:
		"""Chunks for one document. Return None when the document is gone or must not be indexed."""
		return None

	def dependent_refs(self, ref: DocRef) -> list[DocRef]:
		"""Refs that depend on `ref` (used when ref.cascade is True). For LMS: course -> lessons."""
		return []

	def prune(self, ref: DocRef, kept: list[DocRef]):
		"""Delete stored chunks of dependents that no longer exist. `kept` = current dependents."""

	def on_ref_removed(self, ref: DocRef):
		"""Called when `build_chunks` returned None for a cascading ref."""

	# ---- retrieval
	def access_rule(self, user: str) -> AccessRule:
		"""What the user may see. The default allows nothing (safe)."""
		return AccessRule(source=self.key)

	def post_filter(self, user: str, hits: list[Hit]) -> list[Hit]:
		return hits

	def resolve_context(self, ctx: dict, user: str) -> PageContext | None:
		"""Understand the page the user is on. `ctx` has surface, path and desk_route."""
		return None

	def search_scopes(self, user: str, txt: str, limit: int = 20) -> list[dict]:
		"""Scopes (for example courses) for the widget picker."""
		return []
