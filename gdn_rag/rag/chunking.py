"""Split sections into chunks of about `target_chars` characters."""

from __future__ import annotations

from gdn_rag.rag.text import Section


def split_long(text: str, target: int, overlap: int) -> list[str]:
	"""Split one long text at paragraph, line, sentence or space boundaries."""
	text = text.strip()
	if len(text) <= target:
		return [text] if text else []
	pieces: list[str] = []
	start = 0
	while start < len(text):
		end = min(start + target, len(text))
		if end < len(text):
			window = text[start:end]
			cut = -1
			for sep in ("\n\n", "\n", ". ", " "):
				pos = window.rfind(sep)
				if pos > target * 0.5:
					cut = pos + len(sep)
					break
			if cut > 0:
				end = start + cut
		piece = text[start:end].strip()
		if piece:
			pieces.append(piece)
		if end >= len(text):
			break
		start = max(end - overlap, start + 1)
	return pieces


def _section_text(section: Section) -> str:
	if section.heading and section.text:
		return f"{section.heading}\n{section.text}"
	return section.heading or section.text


def pack_sections(sections: list[Section], target: int, overlap: int, min_chars: int) -> list[str]:
	"""Pack sections into chunks. Returns plain texts (without any breadcrumb)."""
	chunks: list[str] = []
	buffer = ""
	for section in sections:
		text = _section_text(section)
		if not text:
			continue
		if len(text) > target:
			if buffer:
				chunks.append(buffer)
				buffer = ""
			chunks.extend(split_long(text, target, overlap))
			continue
		if buffer and len(buffer) + 2 + len(text) > target:
			chunks.append(buffer)
			buffer = text
		else:
			buffer = f"{buffer}\n\n{text}" if buffer else text
	if buffer:
		chunks.append(buffer)

	if len(chunks) > 1:
		kept = [c for c in chunks if len(c) >= min_chars]
		if kept:
			# a tiny chunk is merged into its neighbour instead of being lost
			merged: list[str] = []
			for chunk in chunks:
				if len(chunk) >= min_chars or not merged:
					merged.append(chunk)
				else:
					merged[-1] = f"{merged[-1]}\n\n{chunk}"
			chunks = merged
	return chunks
