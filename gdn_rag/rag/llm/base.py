"""Base classes and errors for chat providers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator


class ProviderError(Exception):
	"""Base class. Messages must never contain keys or headers."""


class ProviderUnavailable(ProviderError):
	"""Network error, timeout, 5xx, 429, or a bad model/url. Try the next provider."""


class ProviderAuthError(ProviderError):
	"""401 / 403 or a missing key."""


class AllProvidersFailed(ProviderError):
	pass


class NotConfigured(ProviderError):
	pass


_SECRET_PATTERNS = [
	re.compile(r"(?i)(authorization|x-api-key|x-goog-api-key)\s*[:=]\s*\S+"),
	re.compile(r"(?i)([?&]key=)[^&\s]+"),
	re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
	re.compile(r"\b(sk|AIza)[A-Za-z0-9_\-]{12,}"),
]


def scrub(text: str, secret: str | None = None) -> str:
	"""Remove anything that looks like a key from a message that may be logged."""
	text = str(text)
	if secret:
		text = text.replace(secret, "***")
	for pattern in _SECRET_PATTERNS:
		text = pattern.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", text)
	return text[:300]


class ChatAdapter(ABC):
	def __init__(self, conf: dict, api_key: str | None):
		self.conf = conf
		self.api_key = api_key
		self.name = conf.get("name", "provider")
		timeout = conf.get("timeout") or {}
		self.timeout = (float(timeout.get("connect", 5)), float(timeout.get("read", 60)))

	@abstractmethod
	def stream(self, messages: list[dict]) -> Iterator[str]:
		"""Yield text pieces. `messages` use roles system / user / assistant."""

	def ping(self) -> str:
		"""A tiny call used by `test-providers`. Returns the first words of the answer."""
		out = ""
		for piece in self.stream(
			[{"role": "user", "content": "Reply with the single word: ok"}]
		):
			out += piece
			if len(out) > 20:
				break
		return out.strip()
