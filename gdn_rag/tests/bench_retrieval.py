"""Load check for retrieval (plan.md phase 4). Not a unit test: run it by hand on a TEST site.

    cd sites && ../env/bin/python ../apps/gdn_rag/gdn_rag/tests/bench_retrieval.py gdntest.localhost [courses] [chunks_per_course]

It adds fake chunks (scope names start with "gdnragbench"), asks many questions as a user who can see all
courses, prints the timings, and removes the fake chunks again.
"""

import random
import statistics
import sys
import time

import frappe

from gdn_rag.rag import retriever
from gdn_rag.rag.config import get_settings
from gdn_rag.rag.normalize import build_search_text
from gdn_rag.rag.types import AccessRule
from gdn_rag.sources.base import BaseSource

PREFIX = "gdnragbench"


class FakeSource(BaseSource):
	key = "lms"

	def __init__(self, public, full):
		super().__init__({})
		self.public, self.full = public, full

	def access_rule(self, user):
		return AccessRule("lms", set(self.public), set(self.full))


def main(site, courses=300, per_course=20):
	frappe.init(site=site)
	frappe.connect()
	random.seed(7)
	vocab = [f"{w}{i}" for i in range(400) for w in ("alpha", "bravo", "delta", "gamma", "python", "variable")] + [
		"certificate", "certification", "lesson", "course", "quiz", "assignment", "video", "teacher", "student", "exam",
		"javascript", "database", "network", "security", "design", "testing", "deploy", "server", "client", "cloud",
	]  # fmt: skip
	settings = get_settings()
	search = settings.search
	names = [f"{PREFIX}-course-{i}" for i in range(courses)]
	rows = []
	for ci, name in enumerate(names):
		for si in range(per_course):
			text = " ".join(random.choices(vocab, k=45))
			title = f"Course: {name} › Lesson {si}"
			content = f"{title}\n\n{text}. JS and UI tips for AI."
			rows.append(
				(
					f"lms|Course Lesson|{name}-{si}|0", "lms", "Course Lesson", f"{name}-{si}", 0, "LMS Course", name,
					"public" if si % 4 == 0 else "restricted", title, f"/lms/courses/{name}/learn/1-{si}", content,
					build_search_text(content, search), "h", frappe.utils.now(),
				)
			)  # fmt: skip
	frappe.db.sql("DELETE FROM `__gdn_rag_chunk` WHERE scope_name LIKE %s", (f"{PREFIX}%",))
	started = time.time()
	for r in rows:
		frappe.db.sql(
			"""INSERT INTO `__gdn_rag_chunk` (chunk_key, source, ref_doctype, ref_name, seq, scope_doctype, scope_name,
			access, title, route, content, search_text, content_hash, modified) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
			r,
		)
	frappe.db.commit()
	print(f"inserted {len(rows)} chunks in {time.time() - started:.1f}s")

	source = FakeSource(public=names, full=names[:20])
	frappe.local.gdn_rag_sources = {"lms": source}
	questions = []
	for _ in range(200):
		questions.append(" ".join(random.choices(vocab, k=random.choice((2, 3, 5)))))  # normal questions
	for _ in range(60):
		questions.append("JS " + " ".join(random.choices(vocab, k=2)))  # short word -> REGEXP scan
	for _ in range(40):
		questions.append("what is a certificate for " + random.choice(vocab))  # stop words + stems
	scope = {"source": "lms", "scope_doctype": "LMS Course", "scope_name": names[3]}

	def measure(label, scope_arg):
		times = []
		for q in questions:
			t = time.perf_counter()
			retriever.retrieve("bench@example.com", q, settings, scope_arg)
			times.append((time.perf_counter() - t) * 1000)
		times.sort()
		print(f"{label:<34} n={len(times)}  p50={statistics.median(times):6.1f} ms  p95={times[int(len(times) * 0.95)]:6.1f} ms  max={times[-1]:6.1f} ms")
		return times[int(len(times) * 0.95)]

	frappe.cache.delete_value(f"gdn_rag:rowcount:bench@example.com")
	p_all = measure("all courses (no scope)", None)
	p_scope = measure("one course selected", scope)
	frappe.db.sql("DELETE FROM `__gdn_rag_chunk` WHERE scope_name LIKE %s", (f"{PREFIX}%",))
	frappe.db.commit()
	print("removed the fake chunks")
	print("RESULT:", "OK (p95 under 150 ms)" if max(p_all, p_scope) < 150 else "SLOW (p95 over 150 ms)")
	frappe.destroy()


if __name__ == "__main__":
	main(sys.argv[1], *(int(x) for x in sys.argv[2:4]))
