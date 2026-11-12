# Adding a new content source

A *source* tells GDN RAG:

1. which documents to index and how to cut them into text pieces (chunks),
2. who may see which chunk,
3. how to understand the page the user is on.

The core code (`rag/`, `api/`, the widget) never imports LMS. It talks to sources only
through `gdn_rag/sources/base.py`. LMS (`sources/lms.py`) is the working example.

## Steps

### 1. Write a class

```python
# my_app/gdn_rag_source.py
import frappe
from gdn_rag.sources.base import BaseSource
from gdn_rag.rag.types import AccessRule, ChunkInput, DocRef, PageContext, ALL
from gdn_rag.rag.text import html_to_text


class CRMLeadSource(BaseSource):
	key = "crm_lead"                     # unique; also used in sources.json
	label = "CRM leads"
	required_apps = ("crm",)             # the source switches itself off if the app is missing
	required_doctypes = ("CRM Lead",)

	def watched_doctypes(self):
		# doctype -> function(doc, event) returning the refs to re-index
		return {"CRM Lead": lambda doc, event: [DocRef(self.key, "CRM Lead", doc.name)]}

	def iter_refs(self, since):
		filters = {"modified": [">=", since]} if since else {}
		for row in frappe.get_all("CRM Lead", filters=filters, fields=["name", "modified"], order_by="modified"):
			yield DocRef(self.key, "CRM Lead", row.name, modified=str(row.modified))

	def build_chunks(self, ref):
		doc = frappe.db.get_value("CRM Lead", ref.name, ["name", "lead_name", "notes"], as_dict=True)
		if not doc:
			return None                   # document is gone: its chunks are deleted
		title = f"Lead: {doc.lead_name}"
		return [ChunkInput(
			seq=0, title=title, content=f"{title}\n\n{html_to_text(doc.notes)}",
			route=f"/app/crm-lead/{doc.name}",
			scope_doctype="CRM Lead", scope_name=doc.name, access="restricted",
		)]

	def access_rule(self, user):
		# restricted chunks are visible in `full_scopes`. Use real permission checks here.
		if frappe.has_permission("CRM Lead", "read", user=user):
			return AccessRule(self.key, full_scopes=ALL)
		return AccessRule(self.key)       # sees nothing

	def post_filter(self, user, hits):
		# optional: remove hits the user may not read, document by document
		return [h for h in hits if frappe.has_permission("CRM Lead", "read", doc=h.ref_name, user=user)]

	def resolve_context(self, ctx, user):
		route = ctx.get("desk_route") or []
		if len(route) >= 3 and route[:2] == ["Form", "CRM Lead"]:
			return PageContext(self.key, "CRM Lead", route[2], f"Lead: {route[2]}",
				pinned=[DocRef(self.key, "CRM Lead", route[2])])
```

### 2. Register it in your app's `hooks.py`

```python
gdn_rag_sources = ["my_app.gdn_rag_source.CRMLeadSource"]
```

`frappe.get_hooks` merges this list with the one in `gdn_rag`.

### 3. Turn it on

Add an entry to `sites/<site>/gdn_rag/sources.json` (or to the committed `sources.json`):

```json
{ "sources": [ { "key": "crm_lead", "enabled": true, "index_mode": "both" } ] }
```

A class with no entry in `sources.json` is **off**.

### 4. Index it

```
bench --site <site> migrate          # clears the cached source list
bench --site <site> gdn-rag reindex --source crm_lead
bench --site <site> gdn-rag ask "..." --dry-run
```

## Rules for a safe source

- **Access is checked when a question is asked**, not when text is indexed. A chunk is
  visible when `access == "public"` and its scope is in `public_scopes`, **or** its scope is
  in `full_scopes`. `ALL` means every scope. Return an empty `AccessRule` when unsure.
- Index only fields every allowed reader may see. Never index notes meant for staff only.
- `build_chunks` must return `None` for a deleted or unwanted document.
- Keep `scope_name` stable and meaningful (for LMS it is the course). It is used by the
  scope picker and to limit a search.
- `route` must start with `/` and not `//`. The widget refuses any other link.
- No top-level `import` of the other app. Import inside methods.
- Cascading: if one document changes many others (course → lessons), give the ref
  `cascade=True` and implement `dependent_refs`, `prune` and `on_ref_removed`.
- `iter_refs(None)` must list everything. `iter_refs(since)` lists changes since that time.
  Put the `modified` time on each ref and return them oldest first.

## Tests to write

- A user without access never gets a chunk (SQL level: `rag/retriever.py`).
- `build_chunks` never reads private fields.
- The source is `is_available() == False` when the app is missing, and nothing errors.
