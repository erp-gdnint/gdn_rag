"""Files source: every .txt / .md file in a folder becomes one indexed document.

Default folder: gdn_rag/knowledge/ (inside this app, so it ships with git).
Override per site in sources.json: { "key": "files", "folder": "/abs/path" }.

Add a file, then run:  bench --site <site> gdn-rag reindex --source files
All files are visible to every logged-in user.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

import frappe

from gdn_rag.rag import chunking, config, db
from gdn_rag.rag.text import Section
from gdn_rag.rag.types import ALL, AccessRule, ChunkInput, DocRef
from gdn_rag.sources.base import BaseSource

SOURCE = "files"
DOCTYPE = "Knowledge File"  # label only, there is no such DocType
SCOPE = "GDAC Documents"
EXTENSIONS = (".txt", ".md")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def _title(filename: str) -> str:
	return re.sub(r"[_\-]+", " ", os.path.splitext(filename)[0]).strip()


class FilesSource(BaseSource):
	key = SOURCE
	label = "GDAC documents (text files)"

	def is_available(self) -> bool:
		return os.path.isdir(self.folder())

	def folder(self) -> str:
		return self.conf.get("folder") or frappe.get_app_path("gdn_rag", "knowledge")

	def _files(self) -> list[str]:
		try:
			return sorted(f for f in os.listdir(self.folder()) if f.lower().endswith(EXTENSIONS))
		except OSError:
			return []

	# ---- indexing
	def iter_refs(self, since):
		if since is not None:
			return iter(())  # no scheduled sync: reindex by command
		self.prune_missing()  # full reindex: forget files that were deleted
		return iter([DocRef(SOURCE, DOCTYPE, f) for f in self._files()])

	def build_chunks(self, ref):
		path = os.path.join(self.folder(), ref.name)
		if not os.path.isfile(path):
			return None
		with open(path, encoding="utf-8", errors="replace") as fh:
			text = fh.read()
		sections: list[Section] = []
		heading, lines = "", []
		for line in text.splitlines():
			m = _HEADING.match(line)
			if m:
				if any(l.strip() for l in lines):
					sections.append(Section(heading, "\n".join(lines).strip()))
				heading, lines = m.group(2), []
			else:
				lines.append(line)
		if any(l.strip() for l in lines):
			sections.append(Section(heading, "\n".join(lines).strip()))

		c = config.get_settings().chunking
		texts = chunking.pack_sections(sections, int(c.target_chars), int(c.overlap_chars), int(c.min_chars))
		title = _title(ref.name)
		return [
			ChunkInput(
				seq=i,
				title=title,
				content=f"{title}\n\n{t}",
				route=f"/lms?doc={quote(ref.name)}",  # files have no page; unique per file so citations stay distinct
				scope_doctype=DOCTYPE,
				scope_name=SCOPE,
				access="public",
			)
			for i, t in enumerate(texts or [text[:2000]])
		]

	def dependent_refs(self, ref):
		return []

	def prune_missing(self):
		"""Delete chunks of files that were removed from the folder."""
		db.delete_scope_refs_not_in(SOURCE, SCOPE, DOCTYPE, self._files())

	# ---- retrieval
	def access_rule(self, user):
		return AccessRule(SOURCE, ALL, ALL)
