"""Try chat providers one by one (by priority) until one works.

A provider that fails is skipped for a while (circuit breaker), so a stopped local
LLM server costs one short connect timeout, not one per question.
"""

from __future__ import annotations

from collections.abc import Iterator

import frappe

from gdn_rag.rag.config import get_config, provider_ready, resolve_secret
from gdn_rag.rag.llm.anthropic import AnthropicAdapter
from gdn_rag.rag.llm.base import (
	AllProvidersFailed,
	ChatAdapter,
	NotConfigured,
	ProviderAuthError,
	ProviderError,
	scrub,
)
from gdn_rag.rag.llm.openai_compatible import OpenAICompatibleAdapter
from gdn_rag.rag.log import get_logger

ADAPTERS: dict[str, type[ChatAdapter]] = {
	"openai_compatible": OpenAICompatibleAdapter,
	"anthropic": AnthropicAdapter,
}

BACKOFF_START = 60
BACKOFF_MAX = 900
AUTH_BACKOFF = 1800
INTERRUPTED = "\n\n_(answer interrupted)_"


def _logger():
	return get_logger()


# ---------------------------------------------------------------- circuit breaker (Redis)


class Breaker:
	"""Remembers which providers are down. Replaceable in tests."""

	def is_down(self, name: str) -> bool:
		return frappe.cache.get_value(f"gdn_rag:down:{name}") is not None

	def mark_down(self, name: str, auth: bool = False) -> int:
		previous = frappe.cache.get_value(f"gdn_rag:backoff:{name}") or 0
		seconds = AUTH_BACKOFF if auth else min(BACKOFF_MAX, previous * 2 if previous else BACKOFF_START)
		frappe.cache.set_value(f"gdn_rag:down:{name}", seconds, expires_in_sec=seconds)
		frappe.cache.set_value(f"gdn_rag:backoff:{name}", seconds, expires_in_sec=3600)
		return seconds

	def mark_ok(self, name: str):
		frappe.cache.delete_value([f"gdn_rag:down:{name}", f"gdn_rag:backoff:{name}"])

	def should_log_auth(self, name: str) -> bool:
		"""True once per 30 minutes per provider."""
		key = f"gdn_rag:authlog:{name}"
		if frappe.cache.get_value(key):
			return False
		frappe.cache.set_value(key, 1, expires_in_sec=1800)
		return True


def make_adapter(conf: dict) -> ChatAdapter:
	cls = ADAPTERS.get(conf.get("adapter", "openai_compatible"))
	if not cls:
		raise NotConfigured(f"unknown adapter {conf.get('adapter')!r}")
	return cls(conf, resolve_secret(conf.get("api_key")))


def stream_chat(
	messages: list[dict],
	providers: list[dict] | None = None,
	breaker: Breaker | None = None,
	adapter_factory=None,
) -> Iterator[tuple[str, str]]:
	"""Yield (provider_name, text_piece).

	- A provider that fails before its first piece is marked down and the next one is tried.
	- A provider that fails after it sent text ends the answer with a short note. We never
	  switch provider in the middle of an answer.
	- If every provider fails, AllProvidersFailed is raised.
	"""
	breaker = breaker or Breaker()
	adapter_factory = adapter_factory or make_adapter
	providers = providers if providers is not None else get_config().chat_providers()
	ready = [p for p in providers if provider_ready(p)]
	if not ready:
		raise NotConfigured("no chat provider is enabled and ready")

	candidates = [p for p in ready if not breaker.is_down(p["name"])]
	if not candidates:
		candidates = ready  # everything is marked down: try anyway, in priority order

	errors: list[str] = []
	for conf in candidates:
		name = conf["name"]
		started = False
		try:
			adapter = adapter_factory(conf)
			for piece in adapter.stream(messages):
				started = True
				yield name, piece
			if not started:
				raise ProviderError("empty answer")
			breaker.mark_ok(name)
			return
		except GeneratorExit:
			raise
		except Exception as e:  # ProviderError, or anything unexpected from one provider
			is_auth = isinstance(e, ProviderAuthError)
			seconds = breaker.mark_down(name, auth=is_auth)
			message = scrub(str(e) if isinstance(e, ProviderError) else f"{type(e).__name__}: {e}")
			errors.append(f"{name}: {message}")
			_logger().warning("gdn_rag: provider %s failed (%s). Skipping it for %ss.", name, message, seconds)
			if is_auth and breaker.should_log_auth(name):
				frappe.log_error(title="GDN RAG: provider rejected its api key", message=f"provider: {name}\n{message}")
			if started:
				yield name, INTERRUPTED
				return
	raise AllProvidersFailed("; ".join(errors) or "no provider answered")
