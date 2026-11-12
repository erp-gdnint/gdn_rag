"""Turn LMS content (HTML, Markdown, Editor.js JSON) into plain text sections."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr|blockquote|pre)>|<br\s*/?>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v ]+")
_BLANKS = re.compile(r"\n{3,}")
_MACRO = re.compile(r"\{\{.*?\}\}", re.S)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")

# Editor.js block types we never read (they are quizzes, assignments, or have no text).
SKIPPED_BLOCKS = {"quiz", "assignment", "program", "delimiter"}


@dataclass
class Section:
	heading: str  # may be empty
	text: str  # plain text, may be empty


def html_to_text(value: str | None) -> str:
	"""Remove HTML tags, keep line breaks for block tags, collapse spaces."""
	if not value:
		return ""
	text = _BLOCK_END.sub("\n", str(value))
	text = _TAGS.sub("", text)
	text = html.unescape(text)
	lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
	return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _list_items(items, depth: int = 0) -> list[str]:
	out: list[str] = []
	for item in items or []:
		if isinstance(item, str):
			out.append(("  " * depth) + "- " + html_to_text(item))
		elif isinstance(item, dict):
			content = item.get("content") or item.get("text") or ""
			if content:
				out.append(("  " * depth) + "- " + html_to_text(content))
			out.extend(_list_items(item.get("items"), depth + 1))
	return out


def _block_text(block: dict) -> tuple[str, str]:
	"""Return (kind, text) for one Editor.js block. kind is 'header' or 'text'."""
	btype = (block.get("type") or "").lower()
	data = block.get("data") or {}
	if btype in SKIPPED_BLOCKS:
		return "text", ""
	if btype == "header":
		return "header", html_to_text(data.get("text"))
	if btype == "paragraph" or btype == "markdown":
		return "text", html_to_text(data.get("text"))
	if btype == "list":
		return "text", "\n".join(_list_items(data.get("items")))
	if btype == "checklist":
		return "text", "\n".join("- " + html_to_text(i.get("text")) for i in data.get("items", []) if isinstance(i, dict))
	if btype == "quote":
		return "text", " — ".join(t for t in (html_to_text(data.get("text")), html_to_text(data.get("caption"))) if t)
	if btype == "code":
		return "text", str(data.get("code") or "").strip()
	if btype == "table":
		rows = []
		for row in data.get("content", []) or []:
			if isinstance(row, list):
				rows.append(" | ".join(html_to_text(str(c)) for c in row))
		return "text", "\n".join(rows)
	if btype == "warning":
		return "text", " ".join(t for t in (html_to_text(data.get("title")), html_to_text(data.get("message"))) if t)
	if btype in ("embed", "upload", "image"):
		return "text", html_to_text(data.get("caption"))
	text = data.get("text")
	return "text", html_to_text(text) if isinstance(text, str) else ""


def editorjs_to_sections(content: str | None) -> list[Section] | None:
	"""Parse Editor.js JSON. Returns None if `content` is empty or not valid Editor.js JSON."""
	if not content or not str(content).strip():
		return None
	try:
		data = json.loads(content)
	except (ValueError, TypeError):
		return None
	blocks = data.get("blocks") if isinstance(data, dict) else None
	if not isinstance(blocks, list):
		return None

	sections: list[Section] = []
	heading = ""
	parts: list[str] = []

	def flush():
		text = "\n".join(p for p in parts if p).strip()
		if heading or text:
			sections.append(Section(heading, text))

	for block in blocks:
		if not isinstance(block, dict):
			continue
		kind, text = _block_text(block)
		if kind == "header":
			flush()
			heading, parts = text, []
		elif text:
			parts.append(text)
	flush()
	return sections


def body_to_sections(body: str | None) -> list[Section]:
	"""Fallback for old lessons: Markdown/HTML in the `body` field."""
	if not body:
		return []
	text = _MACRO.sub("", body)  # {{ YouTubeVideo(...) }}, {{ Quiz(...) }}
	text = html_to_text(text)
	sections: list[Section] = []
	heading = ""
	parts: list[str] = []
	for line in text.split("\n"):
		match = _MD_HEADING.match(line)
		if match:
			if heading or parts:
				sections.append(Section(heading, "\n".join(parts).strip()))
			heading, parts = match.group(1).strip(), []
		else:
			parts.append(line)
	if heading or any(p.strip() for p in parts):
		sections.append(Section(heading, "\n".join(parts).strip()))
	return sections


def lesson_sections(content: str | None, body: str | None) -> list[Section]:
	sections = editorjs_to_sections(content)
	if sections is None or not any(s.text or s.heading for s in sections):
		sections = body_to_sections(body)
	return [s for s in sections if s.heading or s.text]
