"""LMS source: courses and lessons of the Frappe LMS app.

Rules (see plan.md section 8):
- Only these fields are read. `instructor_notes`, `instructor_content`, quizzes,
  questions, options and assignments are NEVER read or indexed.
- Access is decided when a question is asked (not when text is indexed):
	* a chunk is visible if (it is public AND the course is published) OR the course is
	  one of the user's own courses (enrolled or instructor)
	* a Moderator (and Administrator) sees everything
- We do not call LMS helper functions (get_membership, is_instructor ...). We copy their
  rules into set-based SQL, because those helpers read the session user and run one query
  per row, and they are internal to LMS.
- No `import lms` anywhere, so this file is safe on a site without LMS.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

import frappe

from gdn_rag.rag import chunking, config, db
from gdn_rag.rag.text import Section, html_to_text, lesson_sections
from gdn_rag.rag.types import ALL, AccessRule, ChunkInput, DocRef, PageContext
from gdn_rag.sources.base import BaseSource

SOURCE = "lms"
COURSE = "LMS Course"
LESSON = "Course Lesson"
CHAPTER = "Course Chapter"

# The ONLY lesson fields we read. Do not add instructor_notes / instructor_content here.
LESSON_FIELDS = ["name", "title", "course", "chapter", "include_in_preview", "content", "body", "is_scorm_package"]
COURSE_FIELDS = ["name", "title", "short_introduction", "description", "tags", "category", "published"]

ACCESS_TTL = 300  # seconds. Enrollment/publish changes apply within this time.

_RE_LESSON = re.compile(r"^/lms/courses/([^/]+)/learn/(\d+)-(\d+)/?$")
_RE_COURSE_LEARN = re.compile(r"^/lms/courses/([^/]+)/learn/[^/]+/?$")
_RE_COURSE = re.compile(r"^/lms/courses/([^/]+)(?:/(?:certification))?/?$")


def course_route(course: str) -> str:
	return f"/lms/courses/{quote(course, safe='')}"


def lesson_route(course: str, c: int, l: int) -> str:
	return f"{course_route(course)}/learn/{c}-{l}"


class LMSSource(BaseSource):
	key = SOURCE
	label = "LMS courses and lessons"
	required_apps = ("lms",)
	required_doctypes = (
		COURSE,
		CHAPTER,
		LESSON,
		"Chapter Reference",
		"Lesson Reference",
		"LMS Enrollment",
		"Course Instructor",
	)

	# ------------------------------------------------------------ settings helpers
	def _chunk_conf(self):
		return config.get_settings().chunking

	def _search_conf(self):
		return config.get_settings().search

	# ------------------------------------------------------------ indexing: events
	def watched_doctypes(self):
		return {
			COURSE: self._refs_for_course,
			CHAPTER: self._refs_for_chapter,
			LESSON: self._refs_for_lesson,
		}

	def _refs_for_course(self, doc, event):
		return [DocRef(SOURCE, COURSE, doc.name, cascade=True)]

	def _refs_for_chapter(self, doc, event):
		courses = set(
			frappe.get_all("Chapter Reference", filters={"chapter": doc.name, "parenttype": COURSE}, pluck="parent")
		)
		if doc.get("course"):
			courses.add(doc.get("course"))
		return [DocRef(SOURCE, COURSE, c, cascade=True) for c in sorted(courses)]

	def _refs_for_lesson(self, doc, event):
		refs = [DocRef(SOURCE, LESSON, doc.name)]
		if doc.get("course"):
			# the course outline contains lesson titles
			refs.append(DocRef(SOURCE, COURSE, doc.get("course")))
		return refs

	# ------------------------------------------------------------ indexing: scheduler
	def iter_refs(self, since):
		refs: dict[tuple[str, str], DocRef] = {}

		def add(doctype, name, modified, cascade):
			key = (doctype, name)
			old = refs.get(key)
			modified = str(modified) if modified else None
			if old:
				cascade = cascade or old.cascade
				if old.modified and (not modified or old.modified > modified):
					modified = old.modified
			refs[key] = DocRef(SOURCE, doctype, name, cascade=cascade, modified=modified)

		if since is None:
			# full run: every course with its lessons (cascade). Lessons are covered by the cascade.
			for r in frappe.db.sql(f"SELECT name, modified FROM `tab{COURSE}` ORDER BY modified", as_dict=True):
				add(COURSE, r.name, r.modified, True)
		else:
			for r in frappe.db.sql(
				f"SELECT name, modified FROM `tab{COURSE}` WHERE modified >= %s", (since,), as_dict=True
			):
				add(COURSE, r.name, r.modified, True)
			for r in frappe.db.sql(
				f"""SELECT ch.modified, cr.parent AS course FROM `tab{CHAPTER}` ch
				JOIN `tabChapter Reference` cr ON cr.chapter = ch.name AND cr.parenttype = %s
				WHERE ch.modified >= %s""",
				(COURSE, since),
				as_dict=True,
			):
				add(COURSE, r.course, r.modified, True)
			for r in frappe.db.sql(
				f"SELECT name, course, modified FROM `tab{LESSON}` WHERE modified >= %s", (since,), as_dict=True
			):
				add(LESSON, r.name, r.modified, False)
				if r.course:
					add(COURSE, r.course, r.modified, False)
		return iter(sorted(refs.values(), key=lambda r: r.modified or ""))

	# ------------------------------------------------------------ indexing: chunks
	def build_chunks(self, ref):
		if ref.doctype == COURSE:
			return self._course_chunks(ref)
		if ref.doctype == LESSON:
			return self._lesson_chunks(ref)
		return None

	def dependent_refs(self, ref):
		if ref.doctype != COURSE:
			return []
		return [DocRef(SOURCE, LESSON, row.lesson) for row in self._lesson_rows(ref.name)]

	def prune(self, ref, kept):
		if ref.doctype == COURSE:
			db.delete_scope_refs_not_in(SOURCE, ref.name, LESSON, [k.name for k in kept])

	def on_ref_removed(self, ref):
		if ref.doctype == COURSE:
			db.delete_scope(SOURCE, ref.name)

	def _pack(self, sections: list[Section]) -> list[str]:
		c = self._chunk_conf()
		return chunking.pack_sections(sections, int(c.target_chars), int(c.overlap_chars), int(c.min_chars))

	def _chapter_rows(self, course: str):
		return frappe.db.sql(
			"""SELECT cr.idx AS c, cr.chapter, ch.title AS chapter_title, ch.is_scorm_package
			FROM `tabChapter Reference` cr JOIN `tabCourse Chapter` ch ON ch.name = cr.chapter
			WHERE cr.parent = %s AND cr.parenttype = 'LMS Course' ORDER BY cr.idx""",
			(course,),
			as_dict=True,
		)

	def _lesson_rows(self, course: str):
		"""All lessons of a course with their chapter number (c) and lesson number (l)."""
		return frappe.db.sql(
			"""SELECT cr.idx AS c, lr.idx AS l, lr.lesson, cl.title, cl.is_scorm_package
			FROM `tabChapter Reference` cr
			JOIN `tabLesson Reference` lr ON lr.parent = cr.chapter AND lr.parenttype = 'Course Chapter'
			JOIN `tabCourse Lesson` cl ON cl.name = lr.lesson
			WHERE cr.parent = %s AND cr.parenttype = 'LMS Course'
			ORDER BY cr.idx, lr.idx""",
			(course,),
			as_dict=True,
		)

	def _course_chunks(self, ref: DocRef):
		course = frappe.db.get_value(COURSE, ref.name, COURSE_FIELDS, as_dict=True)
		if not course:
			return None
		title = course.title or course.name
		instructors = [
			r[0]
			for r in frappe.db.sql(
				"""SELECT u.full_name FROM `tabCourse Instructor` ci JOIN `tabUser` u ON u.name = ci.instructor
				WHERE ci.parent = %s AND ci.parenttype = 'LMS Course' ORDER BY ci.idx""",
				(course.name,),
			)
			if r[0]
		]
		meta = []
		if course.category:
			meta.append(f"Category: {course.category}")
		if course.tags:
			meta.append(f"Tags: {course.tags}")
		if instructors:
			meta.append(f"Instructors: {', '.join(instructors)}")
		overview = [
			Section("", title),
			Section("", html_to_text(course.short_introduction)),
			Section("", html_to_text(course.description)),
			Section("", "\n".join(meta)),
		]
		outline: list[Section] = []
		lessons_by_chapter: dict[int, list] = {}
		for row in self._lesson_rows(course.name):
			lessons_by_chapter.setdefault(row.c, []).append(row)
		for ch in self._chapter_rows(course.name):
			lines = [f"Lesson {r.c}-{r.l}: {r.title}" for r in lessons_by_chapter.get(ch.c, [])]
			outline.append(Section(f"Chapter {ch.c}: {ch.chapter_title}", "\n".join(lines)))

		route = course_route(course.name)
		chunks: list[ChunkInput] = []

		def add(prefix_title: str, texts: list[str]):
			for text in texts:
				chunks.append(
					ChunkInput(
						seq=len(chunks),
						title=prefix_title,
						content=f"{prefix_title}\n\n{text}",
						route=route,
						scope_doctype=COURSE,
						scope_name=course.name,
						access="public",
					)
				)

		add(f"Course: {title}", self._pack(overview) or [title])
		if outline:
			add(f"Course: {title} › Outline", self._pack(outline))
		return chunks

	def _lesson_chunks(self, ref: DocRef):
		lesson = frappe.db.get_value(LESSON, ref.name, LESSON_FIELDS, as_dict=True)
		if not lesson or not lesson.course:
			return None
		course_title = frappe.db.get_value(COURSE, lesson.course, "title")
		if course_title is None and not frappe.db.exists(COURSE, lesson.course):
			return None
		position = frappe.db.sql(
			"""SELECT cr.idx AS c, lr.idx AS l, ch.title AS chapter_title
			FROM `tabLesson Reference` lr
			JOIN `tabChapter Reference` cr ON cr.chapter = lr.parent AND cr.parenttype = 'LMS Course'
				AND cr.parent = %(course)s
			JOIN `tabCourse Chapter` ch ON ch.name = cr.chapter
			WHERE lr.lesson = %(lesson)s AND lr.parenttype = 'Course Chapter' LIMIT 1""",
			{"course": lesson.course, "lesson": lesson.name},
			as_dict=True,
		)
		if not position:
			return None  # not part of the course outline: never shown by LMS, so not indexed
		pos = position[0]
		breadcrumb = f"Course: {course_title or lesson.course} › Chapter: {pos.chapter_title} › Lesson: {lesson.title}"
		sections = [] if lesson.is_scorm_package else lesson_sections(lesson.content, lesson.body)
		texts = self._pack(sections) or [""]
		access = "public" if lesson.include_in_preview else "restricted"
		route = lesson_route(lesson.course, pos.c, pos.l)
		return [
			ChunkInput(
				seq=i,
				title=breadcrumb,
				content=f"{breadcrumb}\n\n{text}" if text else breadcrumb,
				route=route,
				scope_doctype=COURSE,
				scope_name=lesson.course,
				access=access,
			)
			for i, text in enumerate(texts)
		]

	# ------------------------------------------------------------ access
	def _compute_access(self, user: str) -> dict:
		roles = set(frappe.get_roles(user))
		if user == "Administrator" or "Moderator" in roles:
			return {"moderator": True, "public": [], "full": []}
		public = frappe.get_all(COURSE, filters={"published": 1}, pluck="name")
		enrolled = frappe.get_all("LMS Enrollment", filters={"member": user}, pluck="course")
		teaching = frappe.get_all(
			"Course Instructor", filters={"instructor": user, "parenttype": COURSE}, pluck="parent"
		)
		full = set(enrolled) | set(teaching)
		if self.conf.get("open_access"):
			full |= set(public)  # every published course, all lessons, enrolled or not
		return {"moderator": False, "public": sorted(set(public)), "full": sorted(full)}

	def access_rule(self, user):
		key = f"gdn_rag:access:lms:{user}"
		data = None
		try:
			data = frappe.cache.get_value(key)
		except Exception:
			pass
		if data is None:
			data = self._compute_access(user)
			try:
				frappe.cache.set_value(key, data, expires_in_sec=ACCESS_TTL)
			except Exception:
				pass
		if data["moderator"]:
			return AccessRule(SOURCE, ALL, ALL)
		return AccessRule(SOURCE, set(data["public"]), set(data["full"]))

	@staticmethod
	def _can_see_course(rule: AccessRule, course: str) -> bool:
		def has(scopes):
			return scopes == ALL or course in scopes

		return has(rule.public_scopes) or has(rule.full_scopes)

	@staticmethod
	def _can_see_lesson_chunks(rule: AccessRule, course: str, preview: bool) -> bool:
		def has(scopes):
			return scopes == ALL or course in scopes

		return has(rule.full_scopes) or (bool(preview) and has(rule.public_scopes))

	# ------------------------------------------------------------ context
	def resolve_context(self, ctx, user):
		course = lesson_name = None
		path = ctx.get("path") or ""
		route = ctx.get("desk_route") or []
		if isinstance(path, str) and path.startswith("/lms/"):
			m = _RE_LESSON.match(path)
			if m:
				course = unquote(m.group(1))
				chapter = frappe.db.get_value("Chapter Reference", {"parent": course, "idx": int(m.group(2))}, "chapter")
				if chapter:
					lesson_name = frappe.db.get_value(
						"Lesson Reference", {"parent": chapter, "idx": int(m.group(3))}, "lesson"
					)
			else:
				m = _RE_COURSE_LEARN.match(path) or _RE_COURSE.match(path)
				if m:
					course = unquote(m.group(1))
		elif isinstance(route, list) and len(route) >= 3 and route[0] == "Form":
			doctype, name = route[1], route[2]
			if doctype == COURSE:
				course = name
			elif doctype == LESSON:
				lesson_name = name
				course = frappe.db.get_value(LESSON, name, "course")
			elif doctype == CHAPTER:
				course = frappe.db.get_value(CHAPTER, name, "course")
		if not course:
			return None
		course_row = frappe.db.get_value(COURSE, course, ["name", "title"], as_dict=True)
		if not course_row:
			return None
		rule = self.access_rule(user)
		if not self._can_see_course(rule, course_row.name):
			return None  # do not reveal the title of a course the user cannot see

		label = f"Course: {course_row.title or course_row.name}"
		pinned: list[DocRef] = []
		if lesson_name:
			lesson = frappe.db.get_value(LESSON, lesson_name, ["name", "title", "course", "include_in_preview"], as_dict=True)
			if lesson and lesson.course == course_row.name:
				pos = frappe.db.sql(
					"""SELECT cr.idx AS c, lr.idx AS l FROM `tabLesson Reference` lr
					JOIN `tabChapter Reference` cr ON cr.chapter = lr.parent AND cr.parenttype = 'LMS Course'
						AND cr.parent = %s
					WHERE lr.lesson = %s AND lr.parenttype = 'Course Chapter' LIMIT 1""",
					(course_row.name, lesson.name),
					as_dict=True,
				)
				if pos and self._can_see_lesson_chunks(rule, course_row.name, lesson.include_in_preview):
					label += f" › Lesson {pos[0].c}-{pos[0].l}: {lesson.title}"
					pinned.append(DocRef(SOURCE, LESSON, lesson.name))
		return PageContext(SOURCE, COURSE, course_row.name, label, pinned)

	def search_scopes(self, user, txt, limit=20):
		rule = self.access_rule(user)
		filters: dict = {}
		if not (rule.public_scopes == ALL or rule.full_scopes == ALL):
			names = set(rule.public_scopes) | set(rule.full_scopes)
			if not names:
				return []
			filters["name"] = ["in", sorted(names)]
		txt = re.sub(r"[%_]", "", (txt or "").strip())[:100]
		if txt:
			filters["title"] = ["like", f"%{txt}%"]
		rows = frappe.get_all(COURSE, filters=filters, fields=["name", "title"], order_by="title asc", limit=int(limit))
		return [
			{"source": SOURCE, "scope_doctype": COURSE, "scope_name": r.name, "label": r.title or r.name}
			for r in rows
		]
