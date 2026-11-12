"""Adapter for the Claude Messages API."""

from __future__ import annotations

import json
from collections.abc import Iterator

import requests

from gdn_rag.rag.llm.base import ChatAdapter, ProviderUnavailable, scrub
from gdn_rag.rag.llm.openai_compatible import iter_sse_data, raise_for_status


def split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
	"""Claude wants the system text apart, and user/assistant messages that alternate."""
	system_parts = [m["content"] for m in messages if m.get("role") == "system"]
	merged: list[dict] = []
	for m in messages:
		role = m.get("role")
		if role not in ("user", "assistant"):
			continue
		if merged and merged[-1]["role"] == role:
			merged[-1]["content"] += "\n\n" + m["content"]
		else:
			merged.append({"role": role, "content": m["content"]})
	while merged and merged[0]["role"] != "user":
		merged.pop(0)
	return "\n\n".join(system_parts), merged


class AnthropicAdapter(ChatAdapter):
	def stream(self, messages: list[dict]) -> Iterator[str]:
		base = str(self.conf.get("base_url", "https://api.anthropic.com")).rstrip("/")
		system, msgs = split_messages(messages)
		headers = {
			"Content-Type": "application/json",
			"anthropic-version": "2023-06-01",
			"x-api-key": self.api_key or "",
		}
		body = {
			"model": self.conf.get("model"),
			"max_tokens": int(self.conf.get("max_tokens", 800)),
			"temperature": float(self.conf.get("temperature", 0.2)),
			"messages": msgs,
			"stream": True,
		}
		if system:
			body["system"] = system
		try:
			response = requests.post(f"{base}/v1/messages", headers=headers, json=body, stream=True, timeout=self.timeout)
		except requests.exceptions.RequestException as e:
			raise ProviderUnavailable(scrub(f"{type(e).__name__}: {e}", self.api_key)) from None
		try:
			raise_for_status(response, self.api_key)
			for data in iter_sse_data(response):
				try:
					event = json.loads(data)
				except ValueError:
					continue
				if event.get("type") == "content_block_delta":
					piece = (event.get("delta") or {}).get("text")
					if piece:
						yield piece
				elif event.get("type") == "message_stop":
					return
				elif event.get("type") == "error":
					raise ProviderUnavailable("provider sent an error event")
		except requests.exceptions.RequestException as e:
			raise ProviderUnavailable(scrub(f"{type(e).__name__}: {e}", self.api_key)) from None
		finally:
			response.close()
