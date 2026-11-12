"""Small data classes shared by all parts of GDN RAG."""

from __future__ import annotations

from dataclasses import dataclass, field

ALL = "ALL"  # special value: "every scope" in an AccessRule


@dataclass(frozen=True)
class DocRef:
	"""Points to one document that can be indexed."""

	source: str  # source key, for example "lms"
	doctype: str  # for example "Course Lesson"
	name: str
	# cascade=True: also re-index the documents that depend on this one (course -> lessons)
	cascade: bool = field(default=False, compare=False)
	# modified time, used by the scheduler to move its watermark
	modified: str | None = field(default=None, compare=False)


@dataclass
class ChunkInput:
	seq: int  # 0, 1, 2 ... inside one DocRef
	title: str  # breadcrumb, for example "Python 101 › Basics › Variables"
	content: str  # text that is shown to the LLM (starts with the breadcrumb)
	route: str  # relative url that starts with "/"
	scope_doctype: str  # for example "LMS Course"
	scope_name: str  # for example the course name
	access: str  # "public" or "restricted"


@dataclass
class AccessRule:
	"""Which chunks of one source a user may see.

	- public chunks are visible in `public_scopes`
	- all chunks are visible in `full_scopes`
	Each value is a set of scope names, or the string ALL.
	"""

	source: str
	public_scopes: set[str] | str = field(default_factory=set)
	full_scopes: set[str] | str = field(default_factory=set)


@dataclass
class Hit:
	chunk_id: int
	source: str
	ref_doctype: str
	ref_name: str
	scope_doctype: str
	scope_name: str
	title: str
	route: str
	content: str
	score: float = 0.0


@dataclass
class PageContext:
	source: str
	scope_doctype: str
	scope_name: str
	label: str  # shown in the widget, for example "Course: Python 101"
	pinned: list[DocRef] = field(default_factory=list)  # for example the open lesson
