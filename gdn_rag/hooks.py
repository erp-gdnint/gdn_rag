app_name = "gdn_rag"
app_title = "GDN RAG"
app_publisher = "GDN"
app_description = "RAG for gdn apps"
app_email = "erp@gdn.int"
app_license = "mit"

# Chat widget on Desk pages. Desk loads a tiny loader, which loads the widget with a version in its URL
# (static files are cached for 12 hours). On /lms pages the widget is added by web/inject.py instead,
# because the LMS page is a standalone Vue app that ignores these includes.
app_include_js = ["/assets/gdn_rag/js/gdn_rag_loader.js"]

# Settings for the widget (nothing secret)
extend_bootinfo = "gdn_rag.web.boot.extend_bootinfo"

# Add the widget script to /lms pages
after_request = ["gdn_rag.web.inject.after_request"]

# Install / migrate / uninstall
after_install = "gdn_rag.install.after_install"
after_migrate = "gdn_rag.install.after_migrate"
before_uninstall = "gdn_rag.install.before_uninstall"

# Keep the index fresh when documents change. The handlers return at once for other doctypes.
doc_events = {
	"*": {
		"on_update": "gdn_rag.rag.events.on_update",
		"on_trash": "gdn_rag.rag.events.on_trash",
		"after_rename": "gdn_rag.rag.events.after_rename",
	}
}

scheduler_events = {
	"cron": {
		"*/15 * * * *": ["gdn_rag.rag.jobs.scheduled_sync"],
	},
	"daily_long": ["gdn_rag.rag.jobs.cleanup_orphans"],
	"weekly_long": ["gdn_rag.rag.jobs.optimize_index"],
}

# Content sources. Other apps can add their own class paths to this hook.
gdn_rag_sources = ["gdn_rag.sources.lms.LMSSource"]
