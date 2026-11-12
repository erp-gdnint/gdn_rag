"""Configuration for GDN RAG.

v1 reads JSON files. Later you can replace JsonConfigStore with a store that
reads DocTypes. The rest of the app only talks to `get_config()`.

Layers (a later layer wins):
1. gdn_rag/rag_config/defaults.json
2. site_config.json -> "gdn_rag" key (partial override)
3. sites/<site>/gdn_rag/providers.json  (chat providers; never served over HTTP)
4. gdn_rag/rag_config/sources.json, optional override in sites/<site>/gdn_rag/sources.json
"""

from __future__ import annotations

import copy
import json
import os
import time
from abc import ABC, abstractmethod

import frappe

from gdn_rag.rag.log import get_logger


PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(PACKAGE_DIR, "rag_config")

_FILE_CACHE: dict[str, tuple[float, float, object]] = {}  # path -> (mtime, checked_at, data)
_RECHECK_SECONDS = 5
_warned_literal_keys: set[str] = set()


def _logger():
	return get_logger()


def read_json(path: str, default=None):
	"""Read a JSON file. The result is cached and re-read when the file changes."""
	now = time.time()
	cached = _FILE_CACHE.get(path)
	if cached and now - cached[1] < _RECHECK_SECONDS:
		return cached[2]
	try:
		mtime = os.path.getmtime(path)
	except OSError:
		_FILE_CACHE.pop(path, None)
		return default
	if cached and cached[0] == mtime:
		_FILE_CACHE[path] = (mtime, now, cached[2])
		return cached[2]
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except (OSError, ValueError):
		_logger().error("gdn_rag: cannot read JSON file %s", path)
		return default
	_FILE_CACHE[path] = (mtime, now, data)
	return data


def deep_merge(base, override):
	"""Return a new dict: `override` is merged into `base` (dicts are merged, other values replaced)."""
	result = copy.deepcopy(base)
	for key, value in (override or {}).items():
		if isinstance(value, dict) and isinstance(result.get(key), dict):
			result[key] = deep_merge(result[key], value)
		else:
			result[key] = copy.deepcopy(value)
	return result


def to_dict(value):
	"""Convert nested dicts to frappe._dict so `settings.chat.queue` works."""
	if isinstance(value, dict):
		return frappe._dict({k: to_dict(v) for k, v in value.items()})
	if isinstance(value, list):
		return [to_dict(v) for v in value]
	return value


def resolve_secret(spec):
	"""Turn an api_key setting into the real key.

	- None            -> None (no key, for example a local Ollama)
	- "site_config:X" -> frappe.conf["X"]
	- "env:X"         -> os.environ["X"]
	- anything else   -> used as a literal key (a warning is logged once)
	"""
	if not spec:
		return None
	if not isinstance(spec, str):
		return None
	if spec.startswith("site_config:"):
		return frappe.conf.get(spec.split(":", 1)[1]) or None
	if spec.startswith("env:"):
		return os.environ.get(spec.split(":", 1)[1]) or None
	if spec not in _warned_literal_keys:
		_warned_literal_keys.add(spec)
		_logger().warning(
			"gdn_rag: a provider has a literal api_key in providers.json. "
			"Use 'site_config:<key>' instead."
		)
	return spec


def provider_ready(conf: dict) -> bool:
	"""A provider that needs a key but has none is not ready."""
	spec = conf.get("api_key")
	if spec in (None, "", False):
		return True  # no key needed (local server)
	return bool(resolve_secret(spec))


class ConfigStore(ABC):
	@abstractmethod
	def settings(self) -> frappe._dict: ...

	@abstractmethod
	def chat_providers(self) -> list[dict]: ...

	@abstractmethod
	def sources(self) -> list[dict]: ...


class JsonConfigStore(ConfigStore):
	def _site_file(self, name: str) -> str:
		return frappe.get_site_path("gdn_rag", name)

	def settings(self):
		defaults = read_json(os.path.join(CONFIG_DIR, "defaults.json"), {}) or {}
		site = frappe.conf.get("gdn_rag") or {}
		if not isinstance(site, dict):
			site = {}
		return to_dict(deep_merge(defaults, site))

	def chat_providers(self) -> list[dict]:
		data = read_json(self._site_file("providers.json"), {}) or {}
		providers = [p for p in data.get("chat", []) if isinstance(p, dict) and p.get("enabled")]
		providers.sort(key=lambda p: p.get("priority", 100))
		return copy.deepcopy(providers)

	def sources(self) -> list[dict]:
		base = (read_json(os.path.join(CONFIG_DIR, "sources.json"), {}) or {}).get("sources", [])
		site = (read_json(self._site_file("sources.json"), {}) or {}).get("sources", [])
		merged: dict[str, dict] = {s["key"]: dict(s) for s in base if s.get("key")}
		for s in site:
			if s.get("key"):
				merged[s["key"]] = deep_merge(merged.get(s["key"], {}), s)
		return list(merged.values())


def get_config() -> ConfigStore:
	store = getattr(frappe.local, "gdn_rag_config", None)
	if store is None:
		store = JsonConfigStore()
		frappe.local.gdn_rag_config = store
	return store


def get_settings():
	return get_config().settings()
