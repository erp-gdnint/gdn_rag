"""Turn access rules into a SQL WHERE fragment, and check who may use the assistant."""

from __future__ import annotations

import frappe

from gdn_rag.rag.types import ALL, AccessRule


def build_access_sql(rules: list[AccessRule]) -> tuple[str, dict]:
	"""One SQL condition for all sources. All values are passed as parameters.

	For each rule (public chunks in `public_scopes`, every chunk in `full_scopes`):
	  full == ALL     -> the whole source
	  otherwise       -> (access='public' AND scope IN public) OR scope IN full
	A rule that allows nothing is dropped. If every rule is dropped the result is "1 = 0".
	"""
	branches: list[str] = []
	params: dict = {}
	for i, rule in enumerate(rules):
		src = f"a{i}_src"
		pub, full = rule.public_scopes, rule.full_scopes
		if full == ALL:
			branches.append(f"(source = %({src})s)")
			params[src] = rule.source
			continue
		parts: list[str] = []
		if pub == ALL:
			parts.append("access = 'public'")
		elif pub:
			parts.append(f"(access = 'public' AND scope_name IN %(a{i}_pub)s)")
			params[f"a{i}_pub"] = tuple(sorted(pub))
		if full:
			parts.append(f"scope_name IN %(a{i}_full)s")
			params[f"a{i}_full"] = tuple(sorted(full))
		if parts:
			branches.append(f"(source = %({src})s AND ({' OR '.join(parts)}))")
			params[src] = rule.source
	if not branches:
		return "1 = 0", {}
	return "(" + " OR ".join(branches) + ")", params


def user_allowed(user: str, settings) -> bool:
	"""Logged-in users only. If `allowed_roles` is not empty the user needs one of those roles."""
	if not user or user == "Guest":
		return False
	allowed = settings.get("allowed_roles") or []
	if not allowed:
		return True
	return bool(set(allowed) & set(frappe.get_roles(user)))
