/*
 * GDN RAG loader for Desk (/app).
 * Frappe serves static assets with "Cache-Control: max-age=43200" (12 hours) and adds no version to
 * app_include_js files. So Desk loads only this tiny, rarely changing file. It then loads the real
 * widget with ?v=<file modified time>, which changes whenever the widget changes.
 * (On /lms pages the server adds the same versioned <script> tag itself, see web/inject.py.)
 */
(function () {
	"use strict";
	if (window.__gdnRagLoader) return;
	window.__gdnRagLoader = true;

	function load(tries) {
		var cfg = window.frappe && window.frappe.boot && window.frappe.boot.gdn_rag;
		if (cfg && cfg.enabled) {
			var s = document.createElement("script");
			s.src = "/assets/gdn_rag/js/gdn_rag_widget.js?v=" + encodeURIComponent(cfg.asset_version || "1");
			s.defer = true;
			document.head.appendChild(s);
			return;
		}
		// frappe.boot may not be ready yet. Try for about 10 seconds. If the user is not allowed
		// (no gdn_rag in boot), give up quietly.
		if (tries < 50) setTimeout(function () { load(tries + 1); }, 200);
	}
	load(0);
})();
