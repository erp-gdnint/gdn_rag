"""Builds a small LMS data set for the integration tests.

Everything created here is removed again by `cleanup()`. The data is committed on purpose:
a FULLTEXT index only finds rows after the transaction is committed.
"""

import json

import frappe

PREFIX = "gdnragtest"
PRIVATE_NOTE = "SECRET-INSTRUCTOR-NOTE-9431"
QUIZ_TEXT = "SECRET-QUIZ-ANSWER-7720"


def blocks(*items):
	return json.dumps({"blocks": [{"type": t, "data": d} for t, d in items]})


def paragraphs(*texts, header=None):
	items = []
	if header:
		items.append(("header", {"text": header, "level": 2}))
	items.extend(("paragraph", {"text": t}) for t in texts)
	return blocks(*items)


def make_user(label: str, roles=()) -> str:
	email = f"{PREFIX}.{label}@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"{PREFIX} {label}",
				"send_welcome_email": 0,
				"enabled": 1,
				"roles": [{"role": r} for r in roles],
			}
		).insert(ignore_permissions=True)
	return email


def make_course(title: str, instructor: str, published: int = 1, short: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": f"{PREFIX} {title}",
			"short_introduction": short or f"Short introduction of {title}",
			"description": f"<p>Description of {title}</p>",
			"published": published,
			"instructors": [{"instructor": instructor}],
		}
	).insert(ignore_permissions=True)
	return doc.name


def add_chapter(course: str, title: str, lessons: list[dict]) -> tuple[str, list[str]]:
	"""lessons: [{title, content, preview, notes}] -> (chapter name, [lesson names])"""
	chapter = frappe.get_doc({"doctype": "Course Chapter", "course": course, "title": title}).insert(
		ignore_permissions=True
	)
	names = []
	for item in lessons:
		lesson = frappe.get_doc(
			{
				"doctype": "Course Lesson",
				"chapter": chapter.name,
				"course": course,
				"title": item["title"],
				"content": item.get("content") or "",
				"include_in_preview": 1 if item.get("preview") else 0,
				# these must never be indexed
				"instructor_notes": PRIVATE_NOTE,
				"instructor_content": paragraphs(PRIVATE_NOTE),
			}
		).insert(ignore_permissions=True)
		names.append(lesson.name)
	chapter.reload()
	for name in names:
		chapter.append("lessons", {"lesson": name})
	chapter.save(ignore_permissions=True)
	course_doc = frappe.get_doc("LMS Course", course)
	course_doc.append("chapters", {"chapter": chapter.name})
	course_doc.save(ignore_permissions=True)
	return chapter.name, names


def enroll(user: str, course: str):
	frappe.get_doc({"doctype": "LMS Enrollment", "member": user, "course": course}).insert(ignore_permissions=True)
	forget_access(user)


def forget_access(*users):
	for user in users:
		frappe.cache.delete_value(f"gdn_rag:access:lms:{user}")
		frappe.cache.delete_value(f"gdn_rag:rowcount:{user}")


def cleanup():
	"""Delete everything this module created (matched by the title/email prefix)."""
	frappe.db.rollback()
	courses = frappe.get_all("LMS Course", filters={"title": ["like", f"{PREFIX}%"]}, pluck="name")
	for course in courses:
		for dt in ("LMS Enrollment",):
			for name in frappe.get_all(dt, filters={"course": course}, pluck="name"):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		for name in frappe.get_all("Course Lesson", filters={"course": course}, pluck="name"):
			frappe.delete_doc("Course Lesson", name, force=True, ignore_permissions=True)
		for name in frappe.get_all("Course Chapter", filters={"course": course}, pluck="name"):
			frappe.delete_doc("Course Chapter", name, force=True, ignore_permissions=True)
		frappe.delete_doc("LMS Course", course, force=True, ignore_permissions=True)
	for user in frappe.get_all("User", filters={"email": ["like", f"{PREFIX}.%"]}, pluck="name"):
		forget_access(user)
		frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.sql("DELETE FROM `__gdn_rag_chunk` WHERE scope_name LIKE %s", (f"{PREFIX}%",))
	frappe.db.commit()
