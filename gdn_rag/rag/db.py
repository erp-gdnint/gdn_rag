"""Database layer: plain SQL tables (no DocTypes), works on MariaDB 10.11.

Tables:
- `__gdn_rag_chunk`  text chunks + FULLTEXT index
- `__gdn_rag_state`  small key/value store (sync watermarks, probe result)

Only SQL that MariaDB 10.11 understands is used here.
"""

from __future__ import annotations

import hashlib

import frappe

from gdn_rag.rag.log import get_logger
from gdn_rag.rag.normalize import build_search_text
from gdn_rag.rag.types import ChunkInput, DocRef, Hit

CHUNK = "`__gdn_rag_chunk`"
STATE = "`__gdn_rag_state`"

CREATE_CHUNK = f"""
CREATE TABLE IF NOT EXISTS {CHUNK} (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `chunk_key` VARCHAR(255) NOT NULL,
  `source` VARCHAR(64) NOT NULL,
  `ref_doctype` VARCHAR(140) NOT NULL,
  `ref_name` VARCHAR(140) NOT NULL,
  `seq` INT NOT NULL,
  `scope_doctype` VARCHAR(140) NOT NULL,
  `scope_name` VARCHAR(140) NOT NULL,
  `access` VARCHAR(16) NOT NULL,
  `title` VARCHAR(512) NOT NULL,
  `route` VARCHAR(512) NOT NULL,
  `content` MEDIUMTEXT NOT NULL,
  `search_text` MEDIUMTEXT NOT NULL,
  `content_hash` CHAR(40) NOT NULL,
  `modified` DATETIME(6) NOT NULL,
  UNIQUE KEY `uk_chunk_key` (`chunk_key`),
  KEY `k_ref` (`ref_doctype`, `ref_name`),
  KEY `k_scope` (`source`, `scope_name`, `access`),
  FULLTEXT KEY `ft_search` (`title`, `search_text`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_STATE = f"""
CREATE TABLE IF NOT EXISTS {STATE} (
  `k` VARCHAR(140) NOT NULL PRIMARY KEY,
  `v` TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

PROBE_KEY = "__probe__|probe|probe|0"


def _logger():
	return get_logger()


# ---------------------------------------------------------------- schema


def ensure_schema() -> dict:
	"""Create the tables if missing. Safe to call many times. Returns the stop-word probe result.

	Important: `innodb_ft_enable_stopword = OFF` must be set *while the FULLTEXT index is created*.
	It keeps words like "about", "will" and "who" in the index.
	DDL commits the transaction, so call this only from install, migrate or the CLI.
	"""
	frappe.db.sql_ddl("SET SESSION innodb_ft_enable_stopword = OFF")
	try:
		frappe.db.sql_ddl(CREATE_CHUNK)
		frappe.db.sql_ddl(CREATE_STATE)
	finally:
		frappe.db.sql_ddl("SET SESSION innodb_ft_enable_stopword = ON")
	return run_stopword_probe()


def drop_schema():
	frappe.db.sql_ddl(f"DROP TABLE IF EXISTS {CHUNK}")
	frappe.db.sql_ddl(f"DROP TABLE IF EXISTS {STATE}")


def rebuild_fulltext_index() -> dict:
	"""Drop and re-create the FULLTEXT key with stop words OFF."""
	frappe.db.sql_ddl("SET SESSION innodb_ft_enable_stopword = OFF")
	try:
		try:
			frappe.db.sql_ddl(f"ALTER TABLE {CHUNK} DROP INDEX `ft_search`")
		except Exception:
			pass  # index may not exist
		frappe.db.sql_ddl(f"ALTER TABLE {CHUNK} ADD FULLTEXT KEY `ft_search` (`title`, `search_text`)")
	finally:
		frappe.db.sql_ddl("SET SESSION innodb_ft_enable_stopword = ON")
	return run_stopword_probe()


def run_stopword_probe() -> dict:
	"""Check that the FULLTEXT index finds the word 'about' (a built-in InnoDB stop word)."""
	result = {"ok": False, "error": None}
	try:
		frappe.db.sql(f"DELETE FROM {CHUNK} WHERE chunk_key = %s", (PROBE_KEY,))
		frappe.db.sql(
			f"""INSERT INTO {CHUNK}
			(chunk_key, source, ref_doctype, ref_name, seq, scope_doctype, scope_name, access,
			 title, route, content, search_text, content_hash, modified)
			VALUES (%s, '__probe__', 'probe', 'probe', 0, 'probe', 'probe', 'public',
			 'probe', '/', 'probe', 'about will who probeword', 'x', NOW(6))""",
			(PROBE_KEY,),
		)
		frappe.db.commit()  # FULLTEXT rows become searchable after commit
		rows = frappe.db.sql(
			f"SELECT id FROM {CHUNK} WHERE MATCH(title, search_text) AGAINST ('about' IN BOOLEAN MODE)"
			" AND chunk_key = %s",
			(PROBE_KEY,),
		)
		result["ok"] = bool(rows)
	except Exception as e:
		result["error"] = str(e)[:200]
	finally:
		try:
			frappe.db.sql(f"DELETE FROM {CHUNK} WHERE chunk_key = %s", (PROBE_KEY,))
			frappe.db.commit()
		except Exception:
			pass
	state_set("stopword_probe", "ok" if result["ok"] else "failed")
	if not result["ok"]:
		_logger().warning(
			"gdn_rag: FULLTEXT stop-word probe failed (%s). Run: bench --site <site> gdn-rag rebuild-index",
			result["error"],
		)
	return result


def tables_exist() -> bool:
	"""Ask the catalog instead of querying the table: a failed query makes Frappe print a stack trace."""
	try:
		rows = frappe.db.sql(
			"SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s",
			("__gdn_rag_chunk",),
		)
		return bool(rows)
	except Exception:
		return False


def db_version() -> str:
	try:
		return str(frappe.db.sql("SELECT VERSION()")[0][0])
	except Exception:
		return "unknown"


# ---------------------------------------------------------------- state


def state_get(key: str, default: str | None = None) -> str | None:
	rows = frappe.db.sql(f"SELECT v FROM {STATE} WHERE k = %s", (key,))
	return rows[0][0] if rows else default


def state_set(key: str, value: str):
	frappe.db.sql(
		f"INSERT INTO {STATE} (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v = VALUES(v)",
		(key, value),
	)


# ---------------------------------------------------------------- write


def chunk_key(ref: DocRef, seq: int) -> str:
	return f"{ref.source}|{ref.doctype}|{ref.name}|{seq}"


def replace_chunks(ref: DocRef, chunks: list[ChunkInput], search: dict | None = None, force: bool = False) -> dict:
	"""Make the stored chunks of `ref` equal to `chunks`.

	Unchanged chunks are not touched. Returns counts: added, updated, deleted, unchanged.
	"""
	stats = {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
	existing = {
		r["chunk_key"]: r
		for r in frappe.db.sql(
			f"""SELECT id, chunk_key, content_hash, title, route, access, scope_doctype, scope_name
			FROM {CHUNK} WHERE source = %s AND ref_doctype = %s AND ref_name = %s""",
			(ref.source, ref.doctype, ref.name),
			as_dict=True,
		)
	}
	now = frappe.utils.now()
	seen: set[str] = set()
	for chunk in chunks:
		key = chunk_key(ref, chunk.seq)
		seen.add(key)
		digest = hashlib.sha1(chunk.content.encode("utf-8")).hexdigest()
		old = existing.get(key)
		same_meta = old and (
			old["title"] == chunk.title[:512]
			and old["route"] == chunk.route[:512]
			and old["access"] == chunk.access
			and old["scope_doctype"] == chunk.scope_doctype
			and old["scope_name"] == chunk.scope_name
		)
		if old and not force and old["content_hash"] == digest and same_meta:
			stats["unchanged"] += 1
			continue
		frappe.db.sql(
			f"""INSERT INTO {CHUNK}
			(chunk_key, source, ref_doctype, ref_name, seq, scope_doctype, scope_name, access,
			 title, route, content, search_text, content_hash, modified)
			VALUES (%(key)s, %(source)s, %(rd)s, %(rn)s, %(seq)s, %(sd)s, %(sn)s, %(access)s,
			 %(title)s, %(route)s, %(content)s, %(search_text)s, %(hash)s, %(now)s)
			ON DUPLICATE KEY UPDATE
			 scope_doctype = VALUES(scope_doctype), scope_name = VALUES(scope_name),
			 access = VALUES(access), title = VALUES(title), route = VALUES(route),
			 content = VALUES(content), search_text = VALUES(search_text),
			 content_hash = VALUES(content_hash), modified = VALUES(modified)""",
			{
				"key": key,
				"source": ref.source,
				"rd": ref.doctype,
				"rn": ref.name,
				"seq": chunk.seq,
				"sd": chunk.scope_doctype,
				"sn": chunk.scope_name,
				"access": chunk.access,
				"title": chunk.title[:512],
				"route": chunk.route[:512],
				"content": chunk.content,
				"search_text": build_search_text(chunk.content, search),
				"hash": digest,
				"now": now,
			},
		)
		stats["updated" if old else "added"] += 1

	stale = [r["id"] for k, r in existing.items() if k not in seen]
	if stale:
		frappe.db.sql(f"DELETE FROM {CHUNK} WHERE id IN %(ids)s", {"ids": tuple(stale)})
		stats["deleted"] += len(stale)
	return stats


def delete_ref(ref: DocRef) -> int:
	frappe.db.sql(
		f"DELETE FROM {CHUNK} WHERE source = %s AND ref_doctype = %s AND ref_name = %s",
		(ref.source, ref.doctype, ref.name),
	)
	return frappe.db.sql("SELECT ROW_COUNT()")[0][0]


def delete_scope(source: str, scope_name: str) -> int:
	frappe.db.sql(f"DELETE FROM {CHUNK} WHERE source = %s AND scope_name = %s", (source, scope_name))
	return frappe.db.sql("SELECT ROW_COUNT()")[0][0]


def delete_source(source: str) -> int:
	frappe.db.sql(f"DELETE FROM {CHUNK} WHERE source = %s", (source,))
	return frappe.db.sql("SELECT ROW_COUNT()")[0][0]


def delete_scope_refs_not_in(source: str, scope_name: str, ref_doctype: str, keep_names: list[str]):
	"""Delete chunks of one ref_doctype inside a scope, except the listed names."""
	if keep_names:
		frappe.db.sql(
			f"""DELETE FROM {CHUNK} WHERE source = %(s)s AND scope_name = %(sc)s
			AND ref_doctype = %(rd)s AND ref_name NOT IN %(keep)s""",
			{"s": source, "sc": scope_name, "rd": ref_doctype, "keep": tuple(keep_names)},
		)
	else:
		frappe.db.sql(
			f"DELETE FROM {CHUNK} WHERE source = %s AND scope_name = %s AND ref_doctype = %s",
			(source, scope_name, ref_doctype),
		)


# ---------------------------------------------------------------- read


def _scope_sql(scope: dict | None, params: dict) -> str:
	if not scope:
		return ""
	params["sc_source"] = scope.get("source")
	params["sc_name"] = scope.get("scope_name")
	return " AND source = %(sc_source)s AND scope_name = %(sc_name)s"


_HIT_COLUMNS = "id, source, ref_doctype, ref_name, scope_doctype, scope_name, title, route, content"


def _to_hit(row: dict, score: float = 0.0) -> Hit:
	return Hit(
		chunk_id=row["id"],
		source=row["source"],
		ref_doctype=row["ref_doctype"],
		ref_name=row["ref_name"],
		scope_doctype=row["scope_doctype"],
		scope_name=row["scope_name"],
		title=row["title"],
		route=row["route"],
		content=row["content"],
		score=score,
	)


def fulltext_search(boolean_query: str, access_sql: str, access_params: dict, scope: dict | None, limit: int) -> list[Hit]:
	if not boolean_query:
		return []
	params = dict(access_params)
	params["q"] = boolean_query
	params["n"] = int(limit)
	scope_sql = _scope_sql(scope, params)
	rows = frappe.db.sql(
		f"""SELECT {_HIT_COLUMNS},
			MATCH(title, search_text) AGAINST (%(q)s IN BOOLEAN MODE) AS score
		FROM {CHUNK}
		WHERE {access_sql} {scope_sql}
			AND MATCH(title, search_text) AGAINST (%(q)s IN BOOLEAN MODE)
		ORDER BY score DESC
		LIMIT %(n)s""",
		params,
		as_dict=True,
	)
	return [_to_hit(r, float(r["score"] or 0)) for r in rows]


def regexp_search(pattern: str, access_sql: str, access_params: dict, scope: dict | None, limit: int) -> list[Hit]:
	"""Slow path for 1-2 character words. `pattern` must be built only from [a-z0-9]{1,2} words."""
	params = dict(access_params)
	params["pattern"] = pattern
	params["n"] = int(limit)
	scope_sql = _scope_sql(scope, params)
	rows = frappe.db.sql(
		f"""SELECT {_HIT_COLUMNS} FROM {CHUNK}
		WHERE {access_sql} {scope_sql} AND search_text REGEXP %(pattern)s
		LIMIT %(n)s""",
		params,
		as_dict=True,
	)
	return [_to_hit(r) for r in rows]


def count_accessible(access_sql: str, access_params: dict) -> int:
	return int(frappe.db.sql(f"SELECT COUNT(*) FROM {CHUNK} WHERE {access_sql}", access_params)[0][0])


def ref_chunks(refs: list[DocRef], access_sql: str, access_params: dict, max_seq: int = 1) -> list[Hit]:
	"""First chunks (seq <= max_seq) of the given refs, if the user may see them."""
	hits: list[Hit] = []
	for ref in refs:
		params = dict(access_params)
		params.update({"rs": ref.source, "rd": ref.doctype, "rn": ref.name, "ms": max_seq})
		rows = frappe.db.sql(
			f"""SELECT {_HIT_COLUMNS} FROM {CHUNK}
			WHERE {access_sql} AND source = %(rs)s AND ref_doctype = %(rd)s AND ref_name = %(rn)s
			AND seq <= %(ms)s ORDER BY seq""",
			params,
			as_dict=True,
		)
		hits.extend(_to_hit(r) for r in rows)
	return hits


def distinct_refs(source: str | None = None) -> list[tuple[str, str, str]]:
	where = " WHERE source = %s" if source else ""
	args = (source,) if source else ()
	return [
		(r[0], r[1], r[2])
		for r in frappe.db.sql(f"SELECT DISTINCT source, ref_doctype, ref_name FROM {CHUNK}{where}", args)
	]


def distinct_sources() -> list[str]:
	return [r[0] for r in frappe.db.sql(f"SELECT DISTINCT source FROM {CHUNK}")]


def stats() -> dict:
	by_source = {
		r[0]: {"chunks": r[1], "refs": r[2]}
		for r in frappe.db.sql(
			f"SELECT source, COUNT(*), COUNT(DISTINCT ref_doctype, ref_name) FROM {CHUNK} GROUP BY source"
		)
	}
	return {"by_source": by_source, "total_chunks": sum(v["chunks"] for v in by_source.values())}
