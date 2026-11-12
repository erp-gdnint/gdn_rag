"""bench commands: `bench --site <site> gdn-rag <command>`."""

import click
import frappe
from frappe.commands import get_site, pass_context


def _run(context, fn):
	"""Open the site, run fn(), close the site."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()
	try:
		return fn()
	finally:
		frappe.destroy()


@click.group("gdn-rag")
def gdn_rag():
	"""GDN RAG: index course content and answer questions about it."""


@gdn_rag.command("status")
@pass_context
def status(context):
	"""Show tables, chunk counts, sources, providers and the FULLTEXT stop-word probe."""

	def go():
		from gdn_rag.rag import db, registry
		from gdn_rag.rag.config import get_config, provider_ready

		click.echo(f"MariaDB version : {db.db_version()}")
		click.echo(f"Tables exist    : {db.tables_exist()}")
		if not db.tables_exist():
			click.echo("Run: bench --site <site> migrate")
			return
		probe = db.state_get("stopword_probe")
		click.echo(f"Stop-word probe : {probe or 'not run'}" + ("" if probe == "ok" else "  <-- run: gdn-rag rebuild-index"))
		click.echo(f"Sources         : {', '.join(sorted(registry.get_sources())) or 'none available'}")
		stats = db.stats()
		click.echo(f"Chunks          : {stats['total_chunks']}")
		for key, value in stats["by_source"].items():
			click.echo(f"  {key}: {value['chunks']} chunks, {value['refs']} documents, last sync {db.state_get('sync:' + key) or 'never'}")
		providers = get_config().chat_providers()
		click.echo(f"Chat providers  : {len(providers)} enabled")
		for p in providers:
			down = frappe.cache.get_value(f"gdn_rag:down:{p['name']}")
			state = "ready" if provider_ready(p) else "NOT READY (api key missing)"
			if down:
				state += f", skipped for {down}s after a failure"
			click.echo(f"  {p['priority']:>4}  {p['name']}  {p.get('model')}  [{state}]")
		if not providers:
			click.echo("  none. Copy rag_config/providers.example.json to sites/<site>/gdn_rag/providers.json")

	_run(context, go)


@gdn_rag.command("reindex")
@click.option("--course", help="Only this LMS Course (and its lessons)")
@click.option("--lesson", help="Only this Course Lesson")
@click.option("--source", help="Only this source key, for example lms")
@pass_context
def reindex(context, course, lesson, source):
	"""Rebuild the chunks (all, one course, or one lesson)."""

	def go():
		from gdn_rag.rag import indexer

		if course:
			totals = indexer.reindex_one("lms", "LMS Course", course)
		elif lesson:
			totals = indexer.reindex_one("lms", "Course Lesson", lesson)
		else:
			count = {"n": 0}

			def progress(ref):
				count["n"] += 1
				if count["n"] % 10 == 0:
					click.echo(f"  ... {count['n']} documents")

			totals = indexer.reindex_all(source, force=True, progress=progress)
		click.echo(f"Done: {totals}")

	_run(context, go)


@gdn_rag.command("rebuild-index")
@pass_context
def rebuild_index(context):
	"""Drop and re-create the FULLTEXT key with stop words OFF."""

	def go():
		from gdn_rag.rag import db

		result = db.rebuild_fulltext_index()
		click.echo(f"Stop-word probe: {'ok' if result['ok'] else 'FAILED ' + str(result['error'])}")

	_run(context, go)


@gdn_rag.command("test-providers")
@pass_context
def test_providers(context):
	"""Send one tiny request to each enabled chat provider."""

	def go():
		import time

		from gdn_rag.rag.config import get_config, provider_ready
		from gdn_rag.rag.llm.chain import make_adapter

		providers = get_config().chat_providers()
		if not providers:
			click.echo("No enabled chat provider.")
		for p in providers:
			if not provider_ready(p):
				click.echo(f"{p['name']:<12} SKIP  api key is missing")
				continue
			started = time.monotonic()
			try:
				reply = make_adapter(p).ping()
				click.echo(f"{p['name']:<12} OK    {int((time.monotonic() - started) * 1000)} ms  reply: {reply[:40]!r}")
			except Exception as e:
				from gdn_rag.rag.llm.base import scrub

				click.echo(f"{p['name']:<12} FAIL  {scrub(str(e))}")

	_run(context, go)


@gdn_rag.command("ask")
@click.argument("question")
@click.option("--user", default="Administrator", help="Ask as this user (access rules apply)")
@click.option("--course", help="Limit to this course name")
@click.option("--path", help="Pretend the user is on this page, for example /lms/courses/x/learn/1-2")
@click.option("--dry-run", is_flag=True, help="Only show the search query and hits. Do not call an LLM.")
@pass_context
def ask(context, question, user, course, path, dry_run):
	"""Ask a question from the terminal."""

	def go():
		from gdn_rag.rag import answer, retriever
		from gdn_rag.rag.config import get_settings

		frappe.set_user(user)
		settings = get_settings()
		scope = {"source": "lms", "scope_doctype": "LMS Course", "scope_name": course} if course else None
		ctx = {"surface": "lms", "path": path} if path else None
		if dry_run:
			page = answer.resolve_context(user, ctx)
			used = answer.effective_scope(scope, page)
			hits, debug = retriever.retrieve(user, question, settings, used, page, None)
			click.echo(f"page    : {page.label if page else None}")
			click.echo(f"query   : {debug['query']!r}  short terms: {debug['short_terms']}")
			click.echo(f"hits    : {len(hits)}")
			for i, h in enumerate(hits, 1):
				click.echo(f"  [{i}] {h.title}  ({h.route})  score={h.score:.3f}")
			return
		for event in answer.pipeline(user, question, ctx, scope, None, settings):
			if event["type"] == "hits":
				click.echo(f"[{len(event['hits'])} sources found, query {event['debug']['query']!r}]\n")
			elif event["type"] == "delta":
				click.echo(event["text"], nl=False)
			elif event["type"] == "done":
				click.echo("\n")
				for c in event["citations"]:
					click.echo(f"  [{c['n']}] {c['title']}  {c['route']}" + ("  (related)" if c.get("related") else ""))

	_run(context, go)


commands = [gdn_rag]
