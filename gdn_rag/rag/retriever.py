"""Keyword retrieval with access filtering.

Steps (plan.md section 10.3):
1. access rules of every available source -> one SQL condition
2. FULLTEXT (BOOLEAN MODE) candidates
3. a small REGEXP scan for 1-2 character words (only if the question has such words)
4. merge both lists with Reciprocal Rank Fusion
5. put the chunks of the page the user is looking at first
6. cut to top_k and to max_context_chars
"""

from __future__ import annotations

import re

import frappe

from gdn_rag.rag import db, registry
from gdn_rag.rag.access import build_access_sql
from gdn_rag.rag.normalize import build_query, coverage, title_overlap
from gdn_rag.rag.types import Hit, PageContext

_SHORT_TERM_RE = re.compile(r"^[a-z0-9]{1,2}$")


def _meaningful_tokens(question: str, search: dict) -> int:
	return len(build_query(question, search).tokens)


def effective_query_text(question: str, history: list[dict] | None, search: dict) -> str:
	"""A very short follow-up question ("and the second one?") borrows the words of the last question."""
	if _meaningful_tokens(question, search) < 6 and history:
		for item in reversed(history):
			if item.get("role") == "user" and item.get("content"):
				return f"{question} {item['content']}"
	return question


def _regexp_pattern(terms: list[str]) -> str | None:
	safe = [t for t in terms if _SHORT_TERM_RE.match(t)]
	if not safe:
		return None
	return r"\b(" + "|".join(sorted(set(safe))) + r")\b"


def _rrf(lists: list[list[Hit]], k: int) -> list[Hit]:
	scores: dict[int, float] = {}
	by_id: dict[int, Hit] = {}
	for hits in lists:
		for rank, hit in enumerate(hits, start=1):
			scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
			by_id.setdefault(hit.chunk_id, hit)
	merged = []
	for chunk_id, score in scores.items():
		hit = by_id[chunk_id]
		hit.score = score
		merged.append(hit)
	merged.sort(key=lambda h: h.score, reverse=True)
	return merged


def retrieve(
	user: str,
	question: str,
	settings,
	scope: dict | None = None,
	context: PageContext | None = None,
	history: list[dict] | None = None,
) -> tuple[list[Hit], dict]:
	"""Returns (hits, debug). `scope` is {"source", "scope_doctype", "scope_name"} or None for all."""
	r = settings.retrieval
	search = settings.search
	debug: dict = {"query": None, "short_terms": []}

	sources = registry.get_sources()
	if scope:
		sources = {k: s for k, s in sources.items() if k == scope.get("source")}
	rules = [s.access_rule(user) for s in sources.values()]
	access_sql, access_params = build_access_sql(rules)
	if access_sql == "1 = 0":
		return [], debug

	parsed = build_query(effective_query_text(question, history, search), search)
	debug["query"] = parsed.boolean
	debug["short_terms"] = parsed.short_terms

	lists: list[list[Hit]] = []
	if parsed.boolean:
		ft = db.fulltext_search(parsed.boolean, access_sql, access_params, scope, int(r.fulltext_candidates))
		# The database ranks by relevance that rewards a repeated common word. So many chunks with
		# "animals" can push out the one chunk that has ALL the question words. A second query that
		# REQUIRES every word makes sure such a chunk is always a candidate.
		words = parsed.boolean.split()
		if len(words) >= 2:
			seen = {h.chunk_id for h in ft}
			exact = db.fulltext_search(" ".join("+" + w for w in words), access_sql, access_params, scope, int(r.all_words_candidates))
			ft += [h for h in exact if h.chunk_id not in seen]
		# InnoDB relevance numbers have no fixed scale (they move with index statistics) and reward a
		# word that repeats. So: scale them to 0..1, and mix in "coverage" (how many DIFFERENT question
		# words the chunk has). A title match adds at most `title_boost`.
		top = max((h.score for h in ft), default=0.0) or 1.0
		cov_w = float(r.coverage_weight)
		boost = float(r.title_boost)
		for hit in ft:
			hit.score = (
				(1 - cov_w) * (hit.score / top)
				+ cov_w * coverage(parsed.tokens, hit.content, search.aliases)
				+ boost * title_overlap(parsed.tokens, hit.title, search.aliases)
			)
		ft.sort(key=lambda h: h.score, reverse=True)
		lists.append(ft)

	pattern = _regexp_pattern(parsed.short_terms)
	if pattern and _short_scan_allowed(user, scope, access_sql, access_params, int(r.short_term_scan_max_rows)):
		lists.append(db.regexp_search(pattern, access_sql, access_params, scope, int(r.short_term_candidates)))

	merged = _rrf(lists, int(r.rrf_k)) if lists else []

	# the page the user is looking at (for example the open lesson) comes first
	# (if the open lesson has no match, its first two chunks are added, so "summarize this lesson" works)
	if context and context.pinned:
		pinned_refs = {(p.doctype, p.name) for p in context.pinned}
		first = [h for h in merged if (h.ref_doctype, h.ref_name) in pinned_refs]
		if not first:
			first = db.ref_chunks(context.pinned, access_sql, access_params, max_seq=1)
		first_ids = {h.chunk_id for h in first}
		merged = first + [h for h in merged if h.chunk_id not in first_ids]

	# let each source apply extra rules, then cut
	for source in sources.values():
		merged = source.post_filter(user, merged)
	return _cut(merged, int(r.top_k), int(r.max_context_chars)), debug


def _short_scan_allowed(user, scope, access_sql, access_params, max_rows: int) -> bool:
	"""The REGEXP scan reads every row it may match. Skip it on very large indexes unless a course is selected."""
	if scope:
		return True
	key = f"gdn_rag:rowcount:{user}"
	try:
		count = frappe.cache.get_value(key)
	except Exception:
		count = None
	if count is None:
		count = db.count_accessible(access_sql, access_params)
		try:
			frappe.cache.set_value(key, count, expires_in_sec=600)
		except Exception:
			pass
	return count <= max_rows


def _cut(hits: list[Hit], top_k: int, max_chars: int) -> list[Hit]:
	out: list[Hit] = []
	seen: set[int] = set()
	total = 0
	for hit in hits:
		if hit.chunk_id in seen:
			continue
		if len(out) >= top_k:
			break
		if out and total + len(hit.content) > max_chars:
			continue
		seen.add(hit.chunk_id)
		out.append(hit)
		total += len(hit.content)
	return out
