/*
 * GDN RAG chat widget.
 * - One file, no build step, no framework. Lives in a Shadow DOM, so it does not clash with Desk or LMS CSS.
 * - Open with Ctrl+Alt+I (configurable) or the small round button at the bottom right.
 * - The window can be moved (drag the header) and resized (drag any edge or corner).
 * - Model text is always HTML-escaped before it is formatted. Links come only from the server.
 */
(function () {
	"use strict";
	if (window.__gdnRag) return;
	var currentScript = document.currentScript; // must be read while the script runs

	var MIN_W = 320;
	var MIN_H = 360;
	var MAX_W = 900;
	var POLL_MS = 700;
	var POLL_MAX_MS = 3 * 60 * 1000;
	var QUEUED_NOTE_MS = 30 * 1000;
	var KEEP_MESSAGES = 20;

	function readConfig() {
		try {
			if (window.frappe && window.frappe.boot && window.frappe.boot.gdn_rag) return window.frappe.boot.gdn_rag;
		} catch (e) {
			/* ignore */
		}
		var raw = currentScript && currentScript.getAttribute("data-gdn-rag");
		if (raw) {
			try {
				return JSON.parse(raw);
			} catch (e) {
				return null;
			}
		}
		return null;
	}

	function start(tries) {
		var cfg = readConfig();
		if (cfg && cfg.enabled) return init(cfg);
		// Desk: frappe.boot may not be ready yet. Try for about 10 seconds, then give up quietly.
		var isLms = currentScript && currentScript.getAttribute("data-gdn-rag");
		if (!isLms && tries < 50) setTimeout(function () { start(tries + 1); }, 200);
	}

	// ------------------------------------------------------------------ helpers

	function esc(s) {
		return String(s).replace(/[&<>"']/g, function (c) {
			return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
		});
	}

	function clamp(n, lo, hi) {
		return Math.max(lo, Math.min(hi, n));
	}

	function safeRoute(r) {
		return typeof r === "string" && r.charAt(0) === "/" && r.charAt(1) !== "/" && r.indexOf("\\") === -1;
	}

	function store(kind) {
		try {
			return kind === "local" ? window.localStorage : window.sessionStorage;
		} catch (e) {
			return null;
		}
	}

	function readJson(kind, key, fallback) {
		try {
			var s = store(kind);
			var v = s && s.getItem(key);
			return v ? JSON.parse(v) : fallback;
		} catch (e) {
			return fallback;
		}
	}

	function writeJson(kind, key, value) {
		try {
			var s = store(kind);
			if (s) s.setItem(key, JSON.stringify(value));
		} catch (e) {
			/* storage can be blocked: the widget still works */
		}
	}

	// ------------------------------------------------------------------ safe Markdown subset

	function inline(escaped, cites) {
		var codes = [];
		var s = escaped.replace(/`([^`\n]+)`/g, function (m, c) {
			codes.push("<code>" + c + "</code>");
			return "\u0000" + (codes.length - 1) + "\u0000";
		});
		s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
		s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
		s = s.replace(/\[(\d{1,2})\]/g, function (m, n) {
			var c = (cites || []).filter(function (x) { return String(x.n) === n; })[0];
			if (c && safeRoute(c.route)) {
				return '<sup><a class="cite" href="' + esc(c.route) + '">' + n + "</a></sup>";
			}
			return "<sup>" + n + "</sup>";
		});
		return s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return codes[+i]; });
	}

	function renderBlocks(text, cites) {
		var lines = esc(text).split("\n");
		var out = [];
		var para = [];
		var list = null;
		function flushPara() {
			if (para.length) out.push("<p>" + inline(para.join("<br>"), cites) + "</p>");
			para = [];
		}
		function flushList() {
			if (list) {
				out.push("<" + list.t + ">" + list.items.map(function (i) { return "<li>" + inline(i, cites) + "</li>"; }).join("") + "</" + list.t + ">");
			}
			list = null;
		}
		lines.forEach(function (line) {
			var m;
			if (!line.trim()) { flushPara(); flushList(); return; }
			if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
				flushPara();
				if (!list || list.t !== "ul") { flushList(); list = { t: "ul", items: [] }; }
				list.items.push(m[1]);
				return;
			}
			if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
				flushPara();
				if (!list || list.t !== "ol") { flushList(); list = { t: "ol", items: [] }; }
				list.items.push(m[1]);
				return;
			}
			if ((m = line.match(/^\s{0,3}#{1,6}\s+(.*)$/))) {
				flushPara(); flushList();
				out.push("<p><strong>" + inline(m[1], cites) + "</strong></p>");
				return;
			}
			flushList();
			para.push(line);
		});
		flushPara();
		flushList();
		return out.join("");
	}

	function renderMarkdown(text, cites) {
		var out = [];
		var re = /```([\w-]*)\n?([\s\S]*?)(?:```|$)/g;
		var last = 0;
		var m;
		while ((m = re.exec(text)) !== null) {
			if (m.index > last) out.push(renderBlocks(text.slice(last, m.index), cites));
			out.push("<pre><code>" + esc(m[2].replace(/\n$/, "")) + "</code></pre>");
			last = re.lastIndex;
			if (re.lastIndex === m.index) re.lastIndex++;
		}
		if (last < text.length) out.push(renderBlocks(text.slice(last), cites));
		return out.join("");
	}

	// ------------------------------------------------------------------ styles and markup

	var CSS = [
		":host{all:initial}",
		"*{box-sizing:border-box}",
		".root{--bg:#fff;--fg:#1f2937;--muted:#6b7280;--border:#e5e7eb;--accent:#7c3aed;--accent-fg:#fff;--bubble:#f3f4f6;--hover:#f3f4f6;--shadow:0 10px 40px rgba(0,0,0,.22);",
		"font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:var(--fg)}",
		".root.dark{--bg:#1c1c20;--fg:#e5e7eb;--muted:#9ca3af;--border:#34343b;--accent:#8b5cf6;--bubble:#2a2a31;--hover:#2a2a31;--shadow:0 10px 40px rgba(0,0,0,.6)}",
		"button{font:inherit;color:inherit;cursor:pointer}",
		"button:focus-visible,textarea:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
		".launcher{position:fixed;right:20px;bottom:20px;width:48px;height:48px;border-radius:50%;border:0;background:var(--accent);color:var(--accent-fg);",
		"box-shadow:0 4px 14px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center;z-index:1040;padding:0}",
		".launcher:hover{filter:brightness(1.08)}",
		".launcher[hidden],.panel[hidden]{display:none}",
		".panel{position:fixed;z-index:1040;background:var(--bg);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow);",
		"display:flex;flex-direction:column;overflow:hidden;min-width:" + MIN_W + "px;min-height:" + MIN_H + "px}",
		".panel.docked{border-radius:0;border-top:0;border-bottom:0;border-right:0}",
		".panel.mobile{inset:0;width:100%;height:100%;border-radius:0;border:0;min-width:0;min-height:0}",
		".hdr{display:flex;align-items:center;gap:6px;padding:8px 8px 8px 14px;border-bottom:1px solid var(--border);cursor:move;user-select:none;touch-action:none}",
		".panel.docked .hdr,.panel.mobile .hdr{cursor:default}",
		".title{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
		".icon{border:0;background:transparent;width:30px;height:30px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:var(--muted);padding:0}",
		".icon:hover{background:var(--hover);color:var(--fg)}",
		".ctx{position:relative;padding:8px 12px;border-bottom:1px solid var(--border)}",
		".scope-btn{width:100%;display:flex;align-items:center;gap:6px;text-align:left;border:1px solid var(--border);background:var(--bg);border-radius:8px;padding:6px 10px}",
		".scope-btn:hover{background:var(--hover)}",
		".scope-label{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
		".pop{position:absolute;left:12px;right:12px;top:calc(100% - 4px);z-index:5;background:var(--bg);border:1px solid var(--border);border-radius:8px;box-shadow:var(--shadow);padding:6px;max-height:260px;overflow:auto}",
		".pop[hidden]{display:none}",
		".opt{display:block;width:100%;text-align:left;border:0;background:transparent;padding:7px 8px;border-radius:6px}",
		".opt:hover:not([disabled]){background:var(--hover)}",
		".opt[disabled]{color:var(--muted);cursor:not-allowed}",
		".opt.sel{font-weight:600}",
		".find{width:100%;margin:4px 0;padding:6px 8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}",
		".hint{color:var(--muted);padding:4px 8px;font-size:12px}",
		".msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px}",
		".empty{margin:auto;text-align:center;color:var(--muted);padding:16px}",
		".empty b{display:block;color:var(--fg);margin-bottom:4px}",
		".msg{max-width:92%;border-radius:12px;padding:8px 12px;overflow-wrap:anywhere}",
		".msg.user{align-self:flex-end;background:var(--accent);color:var(--accent-fg);white-space:pre-wrap}",
		".msg.assistant{align-self:flex-start;background:var(--bubble)}",
		".msg p{margin:0 0 6px}.msg p:last-child{margin-bottom:0}",
		".msg ul,.msg ol{margin:0 0 6px;padding-left:20px}",
		".msg pre{margin:6px 0;padding:8px;border-radius:6px;background:rgba(0,0,0,.12);overflow:auto;font-size:12.5px}",
		".msg code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}",
		".msg :not(pre)>code{background:rgba(0,0,0,.1);padding:1px 4px;border-radius:4px}",
		".cite{color:var(--accent);text-decoration:none;font-weight:600}",
		".err{color:#dc2626}.root.dark .err{color:#f87171}",
		".note{color:var(--muted);font-size:12px;margin-top:4px}",
		".srcs{margin-top:8px;padding-top:6px;border-top:1px solid var(--border);font-size:12.5px}",
		".srcs b{color:var(--muted);font-weight:600}",
		".srcs a{display:block;color:var(--accent);text-decoration:none;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
		".srcs a:hover{text-decoration:underline}",
		".dots span{display:inline-block;width:6px;height:6px;margin-right:3px;border-radius:50%;background:var(--muted);animation:b 1.2s infinite}",
		".dots span:nth-child(2){animation-delay:.15s}.dots span:nth-child(3){animation-delay:.3s}",
		"@keyframes b{0%,60%,100%{opacity:.25}30%{opacity:1}}",
		"@media (prefers-reduced-motion:reduce){.dots span{animation:none}}",
		".composer{display:flex;gap:8px;padding:10px 12px 4px;border-top:1px solid var(--border);align-items:flex-end}",
		"textarea{flex:1;resize:none;border:1px solid var(--border);border-radius:8px;padding:8px 10px;background:var(--bg);color:var(--fg);font:inherit;max-height:140px;min-height:38px}",
		".send,.stop{border:0;border-radius:8px;padding:8px 14px;background:var(--accent);color:var(--accent-fg);font-weight:600;height:38px}",
		".stop{background:#6b7280}",
		".send[disabled]{opacity:.5;cursor:not-allowed}",
		".foot{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;padding:2px 14px 8px}",
		".rs{position:absolute;z-index:4;touch-action:none}",
		".rs.n,.rs.s{left:10px;right:10px;height:8px;cursor:ns-resize}.rs.n{top:-3px}.rs.s{bottom:-3px}",
		".rs.e,.rs.w{top:10px;bottom:10px;width:8px;cursor:ew-resize}.rs.e{right:-3px}.rs.w{left:-3px}",
		".rs.ne,.rs.nw,.rs.se,.rs.sw{width:14px;height:14px}",
		".rs.ne{top:-3px;right:-3px;cursor:nesw-resize}.rs.nw{top:-3px;left:-3px;cursor:nwse-resize}",
		".rs.se{bottom:-3px;right:-3px;cursor:nwse-resize}.rs.sw{bottom:-3px;left:-3px;cursor:nesw-resize}",
		".panel.mobile .rs{display:none}",
		".panel.docked .rs:not(.w){display:none}",
		".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}"
	].join("");

	var ICON = {
		chat: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
		close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
		dock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>',
		float: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="14" height="12" rx="2"/><path d="M3 9v9a2 2 0 0 0 2 2h9"/></svg>',
		neu: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>',
		caret: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>'
	};

	var SHELL =
		'<div class="root">' +
		'<button class="launcher" type="button">' + ICON.chat + "</button>" +
		'<div class="panel" role="dialog" aria-label="GDAC Coach" hidden>' +
		["n", "s", "e", "w", "ne", "nw", "se", "sw"].map(function (d) { return '<div class="rs ' + d + '" data-dir="' + d + '"></div>'; }).join("") +
		'<div class="hdr"><div class="title">GDAC Coach</div>' +
		'<button class="icon" type="button" data-act="new" title="New chat" aria-label="New chat">' + ICON.neu + "</button>" +
		'<button class="icon" type="button" data-act="dock" title="Dock to the right side" aria-label="Dock to the right side">' + ICON.dock + "</button>" +
		'<button class="icon" type="button" data-act="close" title="Close (Esc)" aria-label="Close">' + ICON.close + "</button></div>" +
		'<div class="ctx"><button class="scope-btn" type="button" aria-haspopup="true" aria-expanded="false"><span class="scope-label"></span>' + ICON.caret + "</button>" +
		'<div class="pop" hidden></div></div>' +
		'<div class="msgs" role="log" aria-label="Conversation"></div>' +
		'<div class="sr" role="status" aria-live="polite"></div>' +
		'<form class="composer"><textarea rows="1" placeholder="Ask the GDAC Coach..." aria-label="Your question"></textarea>' +
		'<button class="send" type="submit">Send</button><button class="stop" type="button" hidden>Stop</button></form>' +
		'<div class="foot"><span class="count"></span><span class="keys"></span></div>' +
		"</div></div>";

	// ------------------------------------------------------------------ main

	function init(cfg) {
		if (window.__gdnRag) return;
		window.__gdnRag = { version: 1 };

		var host = document.createElement("div");
		host.id = "gdn-rag-root";
		var shadow = host.attachShadow({ mode: "open" });
		shadow.innerHTML = "<style>" + CSS + "</style>" + SHELL;
		var append = function () { document.body.appendChild(host); };
		if (document.body) append(); else document.addEventListener("DOMContentLoaded", append);

		var $ = function (sel) { return shadow.querySelector(sel); };
		var root = $(".root");
		var launcher = $(".launcher");
		var panel = $(".panel");
		var hdr = $(".hdr");
		var msgsEl = $(".msgs");
		var statusEl = $(".sr");
		var form = $(".composer");
		var input = $("textarea");
		var sendBtn = $(".send");
		var stopBtn = $(".stop");
		var scopeBtn = $(".scope-btn");
		var scopeLabel = $(".scope-label");
		var pop = $(".pop");
		var countEl = $(".count");
		var dockBtn = $('[data-act="dock"]');

		var maxChars = cfg.max_question_chars || 2000;
		var histTurns = cfg.history_turns || 4;
		var site = location.host;
		var user = cfg.user || "";
		var CHAT_KEY = "gdn_rag:" + site + ":" + user + ":chat";
		var OPEN_KEY = "gdn_rag:" + site + ":" + user + ":open";
		var GEOM_KEY = "gdn_rag:geom";

		var isOpen = false;
		var busy = false;
		var activeRun = null; // { token, stopped }
		var lastFocus = null;
		var ctxInfo = null;
		var lastSig = null;
		var ctxTimer = null;

		var saved = readJson("session", CHAT_KEY, {});
		var GREETING =
			"Hi! I'm the **GDAC Coach**. I'm here to help and guide you through your GDAC grant and the courses on this platform.\n\n" +
			"You can ask me about any course or lesson, get help with progress reports and MEL concepts (outputs, outcomes, indicators), " +
			"or just share your notes and I'll help you turn them into a clear draft for your mentor or GDN.\n\n" +
			"To give you the right guidance, which track is your grant in (**ORD**, **MIDP** or **JSDF**), and which prize did you receive?";
		function welcome() {
			return { role: "assistant", content: GREETING, status: "done", citations: [] };
		}
		var messages = Array.isArray(saved.messages) && saved.messages.length ? saved.messages : [welcome()];
		var scope = saved.scope || { mode: "all" }; // page | all | course
		var geom = readJson("local", GEOM_KEY, null) || { mode: "float", x: null, y: null, w: 420, h: 620, dockW: 420 };

		// ---------- theme
		function isDark() {
			var de = document.documentElement;
			var t = de.getAttribute("data-theme") || (document.body && document.body.getAttribute("data-theme"));
			if (t) return t === "dark";
			if (de.classList.contains("dark") || (document.body && document.body.classList.contains("dark"))) return true;
			return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
		}
		function applyTheme() { root.classList.toggle("dark", isDark()); }
		applyTheme();
		try {
			new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
		} catch (e) { /* ignore */ }

		// ---------- shortcut text
		var sc = cfg.shortcut || { ctrl: true, alt: true, shift: false, code: "KeyI" };
		var keyName = String(sc.code || "KeyI").replace(/^Key/, "").replace(/^Digit/, "");
		var shortcutText = (sc.ctrl ? "Ctrl+" : "") + (sc.alt ? "Alt+" : "") + (sc.shift ? "Shift+" : "") + keyName;
		launcher.title = "Ask assistant (" + shortcutText + ")";
		launcher.setAttribute("aria-label", launcher.title);
		$(".keys").textContent = shortcutText + " to open/close";
		if (cfg.show_launcher === false) launcher.hidden = true;

		// ---------- geometry (move / resize / dock)
		function isMobile() { return window.innerWidth < 640; }

		function rect() {
			var vw = window.innerWidth;
			var vh = window.innerHeight;
			if (geom.mode === "dock") {
				var dw = clamp(geom.dockW || 420, MIN_W, Math.min(MAX_W, vw - 16));
				return { x: vw - dw, y: 0, w: dw, h: vh };
			}
			var w = clamp(geom.w || 420, MIN_W, Math.min(MAX_W, vw - 16));
			var h = clamp(geom.h || 620, MIN_H, vh - 16);
			var x = geom.x == null ? vw - w - 20 : geom.x;
			var y = geom.y == null ? vh - h - 80 : geom.y;
			return { x: clamp(x, 0, vw - w), y: clamp(y, 0, vh - h), w: w, h: h };
		}

		function applyGeom() {
			var mobile = isMobile();
			panel.classList.toggle("mobile", mobile);
			panel.classList.toggle("docked", geom.mode === "dock" && !mobile);
			dockBtn.innerHTML = geom.mode === "dock" ? ICON.float : ICON.dock;
			var label = geom.mode === "dock" ? "Float as a window" : "Dock to the right side";
			dockBtn.title = label;
			dockBtn.setAttribute("aria-label", label);
			if (mobile) {
				panel.style.left = panel.style.top = panel.style.width = panel.style.height = "";
				return;
			}
			var r = rect();
			panel.style.left = r.x + "px";
			panel.style.top = r.y + "px";
			panel.style.width = r.w + "px";
			panel.style.height = r.h + "px";
		}

		function saveGeom() { writeJson("local", GEOM_KEY, geom); }

		// drag the header to move
		hdr.addEventListener("pointerdown", function (e) {
			if (e.target.closest("button") || geom.mode === "dock" || isMobile() || e.button > 0) return;
			e.preventDefault();
			var r = rect();
			var sx = e.clientX, sy = e.clientY;
			hdr.setPointerCapture(e.pointerId);
			function move(ev) {
				geom.x = r.x + ev.clientX - sx;
				geom.y = r.y + ev.clientY - sy;
				geom.w = r.w; geom.h = r.h;
				applyGeom();
			}
			function up() {
				hdr.removeEventListener("pointermove", move);
				hdr.removeEventListener("pointerup", up);
				hdr.removeEventListener("pointercancel", up);
				var cur = rect();
				geom.x = cur.x; geom.y = cur.y;
				saveGeom();
			}
			hdr.addEventListener("pointermove", move);
			hdr.addEventListener("pointerup", up);
			hdr.addEventListener("pointercancel", up);
		});

		// drag an edge or corner to resize
		Array.prototype.forEach.call(shadow.querySelectorAll(".rs"), function (handle) {
			handle.addEventListener("pointerdown", function (e) {
				var dir = handle.getAttribute("data-dir");
				if (isMobile() || e.button > 0) return;
				if (geom.mode === "dock" && dir !== "w") return;
				e.preventDefault();
				var s = rect();
				var sx = e.clientX, sy = e.clientY;
				var vw = window.innerWidth, vh = window.innerHeight;
				handle.setPointerCapture(e.pointerId);
				function move(ev) {
					var dx = ev.clientX - sx, dy = ev.clientY - sy;
					if (geom.mode === "dock") {
						geom.dockW = clamp(s.w - dx, MIN_W, Math.min(MAX_W, vw - 16));
						applyGeom();
						return;
					}
					var x = s.x, y = s.y, w = s.w, h = s.h;
					if (dir.indexOf("e") > -1) w = s.w + dx;
					if (dir.indexOf("s") > -1) h = s.h + dy;
					if (dir.indexOf("w") > -1) { w = s.w - dx; x = s.x + dx; }
					if (dir.indexOf("n") > -1) { h = s.h - dy; y = s.y + dy; }
					var maxW = Math.min(MAX_W, vw);
					if (w < MIN_W) { if (dir.indexOf("w") > -1) x -= MIN_W - w; w = MIN_W; }
					if (w > maxW) { if (dir.indexOf("w") > -1) x += w - maxW; w = maxW; }
					if (h < MIN_H) { if (dir.indexOf("n") > -1) y -= MIN_H - h; h = MIN_H; }
					if (h > vh) { if (dir.indexOf("n") > -1) y += h - vh; h = vh; }
					geom.x = clamp(x, 0, vw - w); geom.y = clamp(y, 0, vh - h);
					geom.w = w; geom.h = h;
					applyGeom();
				}
				function up() {
					handle.removeEventListener("pointermove", move);
					handle.removeEventListener("pointerup", up);
					handle.removeEventListener("pointercancel", up);
					if (geom.mode !== "dock") { var cur = rect(); geom.x = cur.x; geom.y = cur.y; geom.w = cur.w; geom.h = cur.h; }
					saveGeom();
				}
				handle.addEventListener("pointermove", move);
				handle.addEventListener("pointerup", up);
				handle.addEventListener("pointercancel", up);
			});
		});

		window.addEventListener("resize", applyGeom);

		// ---------- network
		function csrf() {
			return (window.frappe && window.frappe.csrf_token) || window.csrf_token || "";
		}

		function errorText(status, body) {
			if (status === 403) return "You don't have access to the assistant.";
			try {
				var list = JSON.parse(body._server_messages || "[]");
				if (list.length) {
					var m = JSON.parse(list[0]).message || "";
					var plain = String(m).replace(/<[^>]+>/g, "").trim();
					if (plain) return plain;
				}
			} catch (e) { /* ignore */ }
			return "Something went wrong. Please try again.";
		}

		function call(method, args, httpMethod) {
			var url = "/api/method/gdn_rag.api.chat." + method;
			var opts = { credentials: "same-origin", headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf() } };
			if (httpMethod === "GET") {
				url += "?" + new URLSearchParams(args || {}).toString();
			} else {
				opts.method = "POST";
				opts.headers["Content-Type"] = "application/json";
				opts.body = JSON.stringify(args || {});
			}
			return fetch(url, opts).then(function (res) {
				return res.json().catch(function () { return {}; }).then(function (body) {
					if (!res.ok) { var err = new Error(errorText(res.status, body)); err.status = res.status; throw err; }
					return body.message;
				});
			});
		}

		// ---------- page context and scope
		function deskRoute() {
			try {
				return window.frappe && window.frappe.get_route ? window.frappe.get_route() : null;
			} catch (e) { return null; }
		}

		function pageCtx() {
			return { surface: cfg.surface || "", path: location.pathname, desk_route: deskRoute() };
		}

		function refreshContext(force) {
			var sig = location.pathname + "|" + JSON.stringify(deskRoute());
			if (!force && sig === lastSig) return;
			lastSig = sig;
			call("get_context", { context: pageCtx() }).then(function (r) {
				ctxInfo = r && r.context ? r.context : null;
				updateScopeButton();
			}).catch(function () { ctxInfo = null; updateScopeButton(); });
		}

		function scopeText() {
			if (scope.mode === "course") return "Course: " + (scope.label || scope.scope_name);
			if (scope.mode === "all") return "All courses";
			return ctxInfo ? ctxInfo.label : "All courses";
		}

		function updateScopeButton() {
			scopeLabel.textContent = scopeText();
			scopeLabel.title = scopeText();
			renderEmpty();
		}

		function scopeForServer() {
			if (scope.mode === "all") return { all: true };
			if (scope.mode === "course") return { source: scope.source, scope_doctype: scope.scope_doctype, scope_name: scope.scope_name };
			return null; // "this page": the server uses the course of the page
		}

		function persist() {
			var keep = messages.filter(function (m) { return m.content || m.error; }).slice(-KEEP_MESSAGES).map(function (m) {
				return { role: m.role, content: m.content || "", citations: m.citations || [], error: m.error || null };
			});
			writeJson("session", CHAT_KEY, { messages: keep, scope: scope });
		}

		// scope popover
		var findTimer = null;
		function openPop() {
			pop.hidden = false;
			scopeBtn.setAttribute("aria-expanded", "true");
			buildPop();
		}
		function closePop() {
			pop.hidden = true;
			scopeBtn.setAttribute("aria-expanded", "false");
		}
		function option(text, selected, disabled, onClick) {
			var b = document.createElement("button");
			b.type = "button";
			b.className = "opt" + (selected ? " sel" : "");
			b.textContent = text;
			if (disabled) b.disabled = true;
			b.addEventListener("click", function () { onClick(); closePop(); updateScopeButton(); persist(); });
			return b;
		}
		function buildPop() {
			pop.textContent = "";
			pop.appendChild(option(ctxInfo ? "This page: " + ctxInfo.label : "This page (no course detected)", scope.mode === "page", !ctxInfo, function () { scope = { mode: "page" }; }));
			pop.appendChild(option("All courses I can see", scope.mode === "all", false, function () { scope = { mode: "all" }; }));
			var find = document.createElement("input");
			find.className = "find";
			find.type = "search";
			find.placeholder = "Find a course...";
			find.setAttribute("aria-label", "Find a course");
			var results = document.createElement("div");
			pop.appendChild(find);
			pop.appendChild(results);
			function load() {
				call("search_scopes", { txt: find.value }, "GET").then(function (r) {
					results.textContent = "";
					var list = (r && r.scopes) || [];
					if (!list.length) { var h = document.createElement("div"); h.className = "hint"; h.textContent = "No courses found."; results.appendChild(h); }
					list.forEach(function (s) {
						results.appendChild(option(s.label, scope.mode === "course" && scope.scope_name === s.scope_name, false, function () {
							scope = { mode: "course", source: s.source, scope_doctype: s.scope_doctype, scope_name: s.scope_name, label: s.label };
						}));
					});
				}).catch(function () {
					results.textContent = "";
					var h = document.createElement("div"); h.className = "hint"; h.textContent = "Could not load courses."; results.appendChild(h);
				});
			}
			find.addEventListener("input", function () { clearTimeout(findTimer); findTimer = setTimeout(load, 300); });
			load();
		}
		scopeBtn.addEventListener("click", function () { if (pop.hidden) openPop(); else closePop(); });
		shadow.addEventListener("click", function (e) {
			if (!pop.hidden && !e.target.closest(".ctx")) closePop();
		});

		// ---------- messages
		function scrollDown(force) {
			var nearBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 80;
			if (force || nearBottom) msgsEl.scrollTop = msgsEl.scrollHeight;
		}

		function renderEmpty() {
			if (messages.length) return;
			msgsEl.textContent = "";
			var d = document.createElement("div");
			d.className = "empty";
			var b = document.createElement("b");
			b.textContent = "Hello! I'm the GDAC Coach";
			d.appendChild(b);
			d.appendChild(document.createTextNode(
				"I'm here to help you navigate the programme requirements and work through your drafts, reports, and MEL concepts. " +
				"To get started, could you tell me: which track is your grant in (ORD, MIDP, or JSDF), and which prize did you receive? " +
				"(Second and third prizes go through a rescaling process, which affects the first steps of the grant.)"
			));
			msgsEl.appendChild(d);
		}

		function bubbleHtml(m) {
			if (m.role === "user") return null;
			var html = "";
			if (m.content) html += renderMarkdown(m.content, m.citations);
			else if (!m.error && (m.status === "queued" || m.status === "running" || m.status === "streaming")) html += '<span class="dots"><span></span><span></span><span></span></span>';
			if (m.status === "queued" && m.slow) html += '<div class="note">Waiting for a background worker...</div>';
			if (m.error) html += '<div class="err">' + esc(m.error) + "</div>";
			if (m.status === "cancelled") html += '<div class="note">Stopped.</div>';
			return html;
		}

		function renderSources(el, m) {
			var list = (m.citations || []).filter(function (c) { return safeRoute(c.route); });
			if (!list.length) return;
			var box = document.createElement("div");
			box.className = "srcs";
			var head = document.createElement("b");
			head.textContent = list[0].related ? "Related:" : "Sources:";
			box.appendChild(head);
			list.forEach(function (c) {
				var a = document.createElement("a");
				a.href = c.route;
				a.textContent = "[" + c.n + "] " + c.title;
				a.title = c.title;
				box.appendChild(a);
			});
			el.appendChild(box);
		}

		function buildMessage(m) {
			var el = document.createElement("div");
			el.className = "msg " + m.role;
			if (m.role === "user") el.textContent = m.content;
			else { el.innerHTML = bubbleHtml(m); renderSources(el, m); }
			m._el = el;
			return el;
		}

		function renderAll() {
			msgsEl.textContent = "";
			if (!messages.length) { renderEmpty(); return; }
			messages.forEach(function (m) { msgsEl.appendChild(buildMessage(m)); });
			scrollDown(true);
		}

		function updateMessage(m) {
			if (!m._el) return;
			var near = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 80;
			m._el.innerHTML = bubbleHtml(m);
			renderSources(m._el, m);
			if (near) msgsEl.scrollTop = msgsEl.scrollHeight;
		}

		// links to Desk pages use the Desk router; other links open normally
		msgsEl.addEventListener("click", function (e) {
			var a = e.target.closest && e.target.closest("a");
			if (!a) return;
			var href = a.getAttribute("href");
			if (!safeRoute(href)) { e.preventDefault(); return; }
			if (cfg.surface === "desk" && href.indexOf("/app/") === 0 && window.frappe && window.frappe.set_route) {
				e.preventDefault();
				window.frappe.set_route(href.replace(/^\/app\//, "").split("/").map(decodeURIComponent));
			}
		});

		// ---------- sending and polling
		function setBusy(on) {
			busy = on;
			sendBtn.hidden = on;
			stopBtn.hidden = !on;
			input.disabled = false;
		}

		function history() {
			return messages.filter(function (m) { return m.content && !m.error && m.status !== "cancelled"; })
				.slice(-2 * histTurns).map(function (m) { return { role: m.role, content: m.content }; });
		}

		function finish(m, patch) {
			Object.keys(patch || {}).forEach(function (k) { m[k] = patch[k]; });
			if (!m.status || m.status === "queued" || m.status === "running" || m.status === "streaming") m.status = m.error ? "error" : "done";
			activeRun = null;
			setBusy(false);
			updateMessage(m);
			persist();
			if (m.error) statusEl.textContent = m.error; else statusEl.textContent = "Answer ready.";
		}

		function send(text) {
			text = (text || "").trim();
			if (!text || busy) return;
			if (text.length > maxChars) { statusEl.textContent = "The question is too long."; return; }
			var past = history();
			messages.push({ role: "user", content: text });
			var a = { role: "assistant", content: "", status: "queued", citations: [] };
			messages.push(a);
			renderAll();
			input.value = "";
			autosize();
			setBusy(true);
			var run = { token: null, stopped: false };
			activeRun = run;
			call("ask", { question: text, context: pageCtx(), scope: scopeForServer(), history: past }).then(function (r) {
				run.token = r.token;
				if (run.stopped) { call("cancel", { token: run.token }).catch(function () {}); return; }
				poll(a, run, Date.now(), 0);
			}).catch(function (err) {
				finish(a, { error: err.message || "Something went wrong.", status: "error" });
			});
		}

		function poll(a, run, startedAt, fails) {
			if (run.stopped) return;
			if (Date.now() - startedAt > POLL_MAX_MS) { finish(a, { error: "The answer took too long. Please try again.", status: "error" }); return; }
			setTimeout(function () {
				if (run.stopped) return;
				call("poll", { token: run.token }, "GET").then(function (r) {
					if (run.stopped) return;
					if (r.status === "expired") { finish(a, { error: "The answer expired. Please ask again.", status: "error" }); return; }
					a.status = r.status;
					a.slow = r.status === "queued" && Date.now() - startedAt > QUEUED_NOTE_MS;
					if (r.text) a.content = r.text;
					if (r.status === "done") { finish(a, { content: r.text || "", citations: r.citations || [], status: "done" }); return; }
					if (r.status === "error") { finish(a, { error: r.error || "Something went wrong.", status: "error" }); return; }
					if (r.status === "cancelled") { finish(a, { status: "cancelled" }); return; }
					updateMessage(a);
					poll(a, run, startedAt, 0);
				}).catch(function (err) {
					if (err.status === 403) { finish(a, { error: err.message, status: "error" }); return; }
					if (fails >= 3) { finish(a, { error: "Lost connection to the server.", status: "error" }); return; }
					poll(a, run, startedAt, fails + 1);
				});
			}, POLL_MS);
		}

		function stop() {
			if (!activeRun) return;
			var run = activeRun;
			run.stopped = true;
			if (run.token) call("cancel", { token: run.token }).catch(function () {});
			var a = messages[messages.length - 1];
			if (a && a.role === "assistant") finish(a, { status: "cancelled" });
			else { activeRun = null; setBusy(false); }
		}

		function newChat() {
			if (busy) stop();
			messages = [welcome()];
			persist();
			renderAll();
			input.focus();
		}

		// ---------- composer
		function autosize() {
			input.style.height = "auto";
			input.style.height = Math.min(input.scrollHeight + 2, 140) + "px"; // +2: the border
			input.style.overflowY = input.scrollHeight + 2 > 140 ? "auto" : "hidden"; // no scrollbar for short text
			var n = input.value.length;
			countEl.textContent = n > maxChars * 0.8 ? n + " / " + maxChars : "";
		}
		input.addEventListener("input", autosize);
		input.addEventListener("keydown", function (e) {
			if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
		});
		form.addEventListener("submit", function (e) { e.preventDefault(); send(input.value); });
		stopBtn.addEventListener("click", stop);

		// ---------- open / close
		function open() {
			if (isOpen) return;
			isOpen = true;
			lastFocus = document.activeElement;
			applyGeom();
			panel.hidden = false;
			launcher.hidden = true;
			writeJson("session", OPEN_KEY, 1);
			renderAll();
			refreshContext(true);
			clearInterval(ctxTimer);
			ctxTimer = setInterval(function () { refreshContext(false); }, 1000);
			setTimeout(function () { input.focus(); }, 0);
		}
		function close() {
			if (!isOpen) return;
			isOpen = false;
			closePop();
			panel.hidden = true;
			launcher.hidden = cfg.show_launcher === false;
			clearInterval(ctxTimer);
			writeJson("session", OPEN_KEY, 0);
			if (lastFocus && lastFocus.focus && document.contains(lastFocus)) { try { lastFocus.focus(); } catch (e) { /* ignore */ } }
		}
		function toggle() { if (isOpen) close(); else open(); }

		launcher.addEventListener("click", open);
		shadow.addEventListener("click", function (e) {
			var act = e.target.closest && e.target.closest("[data-act]");
			if (!act) return;
			var name = act.getAttribute("data-act");
			if (name === "close") close();
			else if (name === "new") newChat();
			else if (name === "dock") {
				geom.mode = geom.mode === "dock" ? "float" : "dock";
				applyGeom(); saveGeom();
			}
		});
		panel.addEventListener("keydown", function (e) {
			if (e.key !== "Escape") return;
			e.stopPropagation();
			if (!pop.hidden) closePop(); else close();
		});

		// the keyboard shortcut works everywhere on the page (capture phase)
		window.addEventListener("keydown", function (e) {
			if (e.code === (sc.code || "KeyI") && !!e.ctrlKey === !!sc.ctrl && !!e.altKey === !!sc.alt && !!e.shiftKey === !!sc.shift && !e.metaKey) {
				e.preventDefault();
				e.stopPropagation();
				toggle();
			}
		}, true);

		window.__gdnRag = { version: 1, open: open, close: close, toggle: toggle };
		applyGeom();
		updateScopeButton();
		autosize();
		if (readJson("session", OPEN_KEY, 0) === 1) open();
	}

	// Tests (tests/widget_markdown.test.js) set window.__GDN_RAG_TEST__ to reach the renderer.
	if (window.__GDN_RAG_TEST__) {
		window.__GDN_RAG_TEST__.renderMarkdown = renderMarkdown;
		window.__GDN_RAG_TEST__.safeRoute = safeRoute;
		return;
	}

	start(0);
})();
