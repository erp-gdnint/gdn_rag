"""Adapter for any server that speaks the OpenAI chat API.

Works for OpenAI, Ollama, vLLM, LM Studio, llama.cpp server and Gemini's OpenAI endpoint.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import requests

from gdn_rag.rag.llm.base import ChatAdapter, ProviderAuthError, ProviderUnavailable, scrub


def iter_sse_data(response) -> Iterator[str]:
	"""Yield the payload of each `data:` line of a server-sent-events response.

	chunk_size=1: the default (512 bytes) makes requests wait until 512 bytes arrived, so a server
	that does not use chunked transfer would deliver the whole answer at once instead of piece by piece.
	"""
	for raw in response.iter_lines(chunk_size=1, decode_unicode=False):
		if not raw:
			continue
		line = raw.decode("utf-8", errors="replace").strip()
		if line.startswith("data:"):
			yield line[5:].strip()


def raise_for_status(response, secret: str | None):
	status = response.status_code
	if status < 400:
		return
	if status in (401, 403):
		raise ProviderAuthError(f"HTTP {status} (check the api key)")
	detail = ""
	try:
		detail = scrub(response.text[:200], secret)
	except Exception:
		pass
	raise ProviderUnavailable(f"HTTP {status} {detail}".strip())


class OpenAICompatibleAdapter(ChatAdapter):
	def stream(self, messages: list[dict]) -> Iterator[str]:
		base = str(self.conf.get("base_url", "")).rstrip("/")
		if not base:
			raise ProviderUnavailable("base_url is not set")
		headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		body = {
			"model": self.conf.get("model"),
			"messages": messages,
			"stream": True,
			"max_tokens": int(self.conf.get("max_tokens", 800)),
			"temperature": float(self.conf.get("temperature", 0.2)),
		}
		try:
			response = requests.post(
				f"{base}/chat/completions", headers=headers, json=body, stream=True, timeout=self.timeout
			)
		except requests.exceptions.RequestException as e:
			raise ProviderUnavailable(scrub(f"{type(e).__name__}: {e}", self.api_key)) from None
		try:
			raise_for_status(response, self.api_key)
			for data in iter_sse_data(response):
				if data == "[DONE]":
					return
				try:
					event = json.loads(data)
				except ValueError:
					continue
				choices = event.get("choices") or []
				if not choices:
					continue
				piece = (choices[0].get("delta") or {}).get("content")
				if piece:
					yield piece
		except requests.exceptions.RequestException as e:
			raise ProviderUnavailable(scrub(f"{type(e).__name__}: {e}", self.api_key)) from None
		finally:
			response.close()
