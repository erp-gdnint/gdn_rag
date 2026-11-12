// Run with: node gdn_rag/tests/widget_markdown.test.js
// Checks that model output can never create HTML or unsafe links in the widget.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "js", "gdn_rag_widget.js"), "utf8");
const sandbox = { __GDN_RAG_TEST__: {}, document: { currentScript: null }, setTimeout };
sandbox.window = sandbox;
new Function("window", "document", "setTimeout", src)(sandbox, sandbox.document, setTimeout);
const { renderMarkdown, safeRoute } = sandbox.__GDN_RAG_TEST__;

function run(name, fn) {
	fn();
	console.log("ok   " + name);
}

run("raw HTML is escaped", () => {
	const out = renderMarkdown("<img src=x onerror=alert(1)> and <script>alert(2)</script>", []);
	assert(!/<img|<script/i.test(out), out);
	assert(out.includes("&lt;img"), out);
});

run("javascript: markdown links are not turned into links", () => {
	const out = renderMarkdown("[click](javascript:alert(1))", []);
	assert(!/<a /i.test(out), out);
});

run("citations link only to safe server routes", () => {
	const cites = [
		{ n: 1, route: "/lms/courses/x/learn/1-2" },
		{ n: 2, route: "//evil.example.com" },
		{ n: 3, route: "javascript:alert(1)" },
	];
	const out = renderMarkdown("See [1] and [2] and [3]", cites);
	assert(out.includes('href="/lms/courses/x/learn/1-2"'), out);
	assert(!out.includes("evil.example.com"), out);
	assert(!out.includes("javascript:"), out);
});

run("quotes in a route cannot break out of the attribute", () => {
	const out = renderMarkdown("[1]", [{ n: 1, route: '/a" onmouseover="alert(1)' }]);
	assert(!out.includes('" onmouseover'), out);
});

run("code is kept as text", () => {
	const out = renderMarkdown("Use `<b>x</b>` here\n\n```js\n<div onclick=1>\n```", []);
	assert(out.includes("<code>&lt;b&gt;x&lt;/b&gt;</code>"), out);
	assert(out.includes("<pre><code>&lt;div onclick=1&gt;</code></pre>"), out);
});

run("lists, bold and italic work", () => {
	const out = renderMarkdown("**Bold** and *it*\n\n- one\n- two\n\n1. a\n2. b", []);
	assert(out.includes("<strong>Bold</strong>"), out);
	assert(out.includes("<em>it</em>"), out);
	assert(out.includes("<ul><li>one</li><li>two</li></ul>"), out);
	assert(out.includes("<ol><li>a</li><li>b</li></ol>"), out);
});

run("unfinished code block while streaming does not hang", () => {
	const out = renderMarkdown("text\n```py\nprint(1)", []);
	assert(out.includes("<pre><code>print(1)</code></pre>"), out);
});

run("safeRoute", () => {
	assert(safeRoute("/lms/courses/a"));
	assert(!safeRoute("//x.com"));
	assert(!safeRoute("https://x.com"));
	assert(!safeRoute("/a\\b"));
	assert(!safeRoute(null));
});

console.log("all widget tests passed");
