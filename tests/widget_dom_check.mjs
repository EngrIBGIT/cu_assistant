// Functional exercise of app/static/widget.js.
//
// The widget is the only part of this project with no Python behind it, so the
// test suite could previously only grep it for strings — which proves the file
// was written, not that it works. This runs it.
//
// A minimal DOM stands in for the browser: just the surface the widget actually
// touches, listed below. It is deliberately dumb. If the widget starts using
// something new, this fails loudly rather than silently passing.
//
// Run directly:  node tests/widget_dom_check.mjs
// Exit code 0 on success, 1 on the first failure.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "app", "static", "widget.js"), "utf8");

let failures = 0;
function check(label, condition, detail) {
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${label}${detail ? `\n         ${detail}` : ""}`);
  }
}
function section(name) {
  console.log(`\n${name}`);
}

// ------------------------------------------------------------------ fake DOM

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    const self = this;
    this.classList = {
      add(...names) {
        const set = new Set(String(self.className).split(/\s+/).filter(Boolean));
        names.forEach((n) => set.add(n));
        self.className = [...set].join(" ");
      },
      remove(name) {
        self.className = String(self.className)
          .split(/\s+/)
          .filter((n) => n && n !== name)
          .join(" ");
      },
      contains(name) {
        return String(self.className).split(/\s+/).includes(name);
      },
    };
  }
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((c) => c.textContent).join("");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.ownerDocument?.register(this);
    if (name === "hidden") this.hidden = true;
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  hasAttribute(name) {
    return name in this.attributes;
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "hidden") this.hidden = false;
  }
  appendChild(node) {
    this.children.push(node);
    node.parent = this;
    return node;
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  focus() {
    this.ownerDocument.activeElement = this;
  }
  requestSubmit() {
    this._fire("submit", { preventDefault() {}, type: "submit" });
  }
  _fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) fn(event);
  }
  // Depth-first search, for tests and assertions.
  find(predicate) {
    if (predicate(this)) return this;
    for (const child of this.children) {
      const hit = child.find(predicate);
      if (hit) return hit;
    }
    return null;
  }
  findAll(predicate, out = []) {
    if (predicate(this)) out.push(this);
    for (const child of this.children) child.findAll(predicate, out);
    return out;
  }
  hasClass(name) {
    return String(this.className).split(/\s+/).includes(name);
  }
}

class Doc {
  constructor() {
    this.byId = new Map();
    this.readyState = "complete";
    this.activeElement = null;
    this.head = new El("head");
    this.body = new El("body");
    this.head.ownerDocument = this;
    this.body.ownerDocument = this;
  }
  register(node) {
    const id = node.getAttribute("id");
    if (id) this.byId.set(id, node);
  }
  createElement(tag) {
    const node = new El(tag);
    node.ownerDocument = this;
    return node;
  }
  getElementById(id) {
    return this.byId.get(id) ?? null;
  }
  addEventListener(type, fn) {
    (this.listeners ||= {})[type] = fn;
  }
}

class Ev {
  constructor(type, init = {}) {
    this.type = type;
    Object.assign(this, init);
  }
}

// -------------------------------------------------------------- canned reply

const GREETING_REPLY = {
  question: "hi",
  answer: "Hello.\n\nI can help you find:\n\n- the right portal\n- who to contact",
  intent: "conversational",
  grounding: "abstained",
  abstained: true,
  abstention_reason: "conversational: greeting",
  primary_route: null,
  also_consider: [],
  citations: [],
  disclaimer: "Check time-sensitive facts.",
  mode: "full",
  version: "1.0.0",
};

const ROUTE_REPLY = {
  question: "how do i apply",
  answer: "**Admissions landing page**\nOffice: Admissions Office",
  intent: "route",
  grounding: "quoted",
  abstained: false,
  abstention_reason: null,
  primary_route: {
    route_id: "admissions-landing",
    label: "Admissions landing page",
    office: "Admissions Office",
    entry_point: "https://admission.cosmopolitan.edu.ng/",
    email: "admission@cosmopolitan.edu.ng",
    phone: "+234 805 208 0828",
    source_url: "https://admission.cosmopolitan.edu.ng/",
  },
  also_consider: [],
  citations: [
    {
      source_url: "https://admission.cosmopolitan.edu.ng/",
      source_title: "Admissions",
      retrieved_at: "2026-09-26",
    },
  ],
  disclaimer: "Check time-sensitive facts.",
  mode: "full",
  version: "1.0.0",
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// Run the widget in a fresh sandbox and return what the page ended up holding.
function run({ scriptSrc, apiBase, reply, failWith, inline }) {
  const doc = new Doc();
  if (inline) {
    const root = doc.createElement("div");
    root.setAttribute("id", "cra-root");
    doc.body.appendChild(root);
  }
  doc.currentScript = scriptSrc ? { src: scriptSrc } : null;

  const calls = [];
  const sandbox = {
    document: doc,
    window: { CU_ROUTE_API: apiBase ?? "" },
    Event: Ev,
    console,
    fetch: (url, init) => {
      calls.push({ url, init });
      if (failWith) return Promise.reject(new Error(failWith));
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(reply),
      });
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "widget.js" });
  return { doc, calls, sandbox };
}

const formOf = (node) => node.find((n) => n.hasClass("cra-form"));
const logOf = (node) => node.find((n) => n.hasClass("cra-log"));

// ---------------------------------------------------------------------------

const CROSS_ORIGIN = "https://embed.example.org/assets/widget.js";

section("Stylesheet is resolved against the script's own origin");
{
  // The bug this catches: document.currentScript is null by DOMContentLoaded,
  // so reading it inside the handler gave "/widget.css", which 404s for any
  // embedder not served from the same origin as the widget.
  const { doc } = run({ scriptSrc: CROSS_ORIGIN, reply: GREETING_REPLY });
  const link = doc.head.children.find((n) => n.tagName === "LINK");
  const href = link?.getAttribute("href") || "";
  check("a stylesheet link was injected", !!link);
  // The origin is the point of this check. The version query is asserted as
  // present but not as a particular number: it is a cache-buster that changes
  // whenever the stylesheet does, and pinning the literal meant a stylesheet
  // change could not ship without editing the test that is supposed to be
  // testing the stylesheet at all. The prefix still fails if the href resolves
  // against the page root, which is the regression this section exists for.
  check(
    "it points at the script's origin, not the page root",
    href.startsWith("https://embed.example.org/assets/widget.css?v="),
    `got ${link?.getAttribute("href")}`
  );
}

section("Stylesheet is not injected twice");
{
  const { sandbox, doc } = run({ scriptSrc: CROSS_ORIGIN, reply: GREETING_REPLY });
  vm.runInContext(source, sandbox, { filename: "widget.js-again" });
  const links = doc.head.children.filter((n) => n.tagName === "LINK");
  check("still exactly one stylesheet link", links.length === 1, `got ${links.length}`);
  check("still exactly one launcher", doc.body.findAll((n) => n.hasClass("cra-launcher")).length === 1);
}

section("Floating mode, when the host page provides no mount point");
{
  const { doc } = run({ scriptSrc: CROSS_ORIGIN, apiBase: "https://api.example.org", reply: GREETING_REPLY });
  const launcher = doc.getElementById("cu-route-assistant");
  const panel = doc.getElementById("cra-panel");
  check("a launcher was created", !!launcher);
  check("a panel was created", !!panel);
  check("the panel starts hidden", panel?.hasAttribute("hidden"));
  check("the launcher says it is collapsed", launcher?.getAttribute("aria-expanded") === "false");
  check("the launcher is labelled for screen readers", launcher?.getAttribute("aria-controls") === "cra-panel");

  launcher._fire("click");
  check("clicking it opens the panel", !panel.hasAttribute("hidden"));
  check("and marks itself expanded", launcher.getAttribute("aria-expanded") === "true");
  check("and moves focus into the panel", doc.activeElement === formOf(panel)?.find((n) => n.hasClass("cra-input")));

  doc.listeners.keydown({ key: "Escape" });
  check("Escape closes it again", panel.hasAttribute("hidden"));
  check("and returns focus to the launcher", doc.activeElement === launcher);
}

section("Inline mode, when the host page provides #cra-root");
{
  const { doc } = run({ scriptSrc: CROSS_ORIGIN, apiBase: "https://api.example.org", reply: GREETING_REPLY, inline: true });
  const root = doc.getElementById("cra-root");
  check("the root element was adopted", root.hasClass("cra-root"));
  check("no floating launcher was added", !doc.getElementById("cu-route-assistant"));
  check("no floating panel was added", !doc.getElementById("cra-panel"));
  check("a form was placed inside the root", !!formOf(root));
  check("a log was placed inside the root", !!logOf(root));
  check("an opening message is present", /Ask me where to apply/.test(logOf(root).textContent));
}

section("Asking a question");
{
  const { doc, calls } = run({
    scriptSrc: CROSS_ORIGIN,
    apiBase: "https://api.example.org",
    reply: ROUTE_REPLY,
    inline: true,
  });
  const root = doc.getElementById("cra-root");
  const form = formOf(root);
  const input = form.find((n) => n.hasClass("cra-input"));
  input.value = "how do i apply";
  form._fire("submit", { preventDefault() {} });
  await flush();

  check("the API was called", calls.length === 1, `got ${calls.length} calls`);
  check("with the right URL", calls[0]?.url === "https://api.example.org/api/ask", `got ${calls[0]?.url}`);
  check("as JSON POST", calls[0]?.init?.method === "POST" && calls[0]?.init?.body === '{"question":"how do i apply"}');
  check("the question is echoed into the log", /how do i apply/.test(logOf(root).textContent));

  const answer = root.find((n) => n.hasClass("cra-answer"));
  check("an answer block was rendered", !!answer);
  check("the route label is shown", /Admissions landing page/.test(answer.textContent));
  check("the bold marker is not shown raw", !answer.textContent.includes("**"));
  check("the source is shown", !!answer.find((n) => n.hasClass("cra-src")));
  check("the source link is safe to open", answer.find((n) => n.hasClass("cra-src"))?.getAttribute("rel") === "noopener");
  check("the citation is shown", /https:\/\/admission\.cosmopolitan\.edu\.ng\//.test(answer.textContent));
  check("the input was cleared", input.value === "");
}

section("An empty question is not sent anywhere");
{
  const { doc, calls } = run({ scriptSrc: CROSS_ORIGIN, apiBase: "https://api.example.org", reply: ROUTE_REPLY, inline: true });
  const form = formOf(doc.getElementById("cra-root"));
  form.find((n) => n.hasClass("cra-input")).value = "   ";
  form._fire("submit", { preventDefault() {} });
  await flush();
  check("no API call was made", calls.length === 0, `got ${calls.length} calls`);
}

section("A greeting is not announced as unpublished");
{
  const { doc } = run({ scriptSrc: CROSS_ORIGIN, apiBase: "https://api.example.org", reply: GREETING_REPLY, inline: true });
  const root = doc.getElementById("cra-root");
  const form = formOf(root);
  form.find((n) => n.hasClass("cra-input")).value = "hi";
  form._fire("submit", { preventDefault() {} });
  await flush();

  const answer = root.find((n) => n.hasClass("cra-answer"));
  check("an answer block was rendered", !!answer);
  check(
    "no 'not published' status line",
    !answer.findAll((n) => n.hasClass("cra-status-note")).length,
    answer.findAll((n) => n.hasClass("cra-status-note")).map((n) => n.textContent).join(" | ")
  );
  check("the bullet list rendered as a list", !!answer.find((n) => n.hasClass("cra-list")));
  check(
    "with both items",
    answer.findAll((n) => n.tagName === "LI").length === 2,
    `got ${answer.findAll((n) => n.tagName === "LI").length}`
  );
  check("and no raw dashes left on screen", !answer.textContent.includes("- the right portal"));
}

section("When the API is unreachable the widget still offers a way forward");
{
  const { doc } = run({
    scriptSrc: CROSS_ORIGIN,
    apiBase: "https://api.example.org",
    reply: ROUTE_REPLY,
    failWith: "network down",
    inline: true,
  });
  const root = doc.getElementById("cra-root");
  const form = formOf(root);
  form.find((n) => n.hasClass("cra-input")).value = "how do i apply";
  form._fire("submit", { preventDefault() {} });
  await flush();

  const error = root.find((n) => n.hasClass("cra-error"));
  check("an error is shown rather than nothing", !!error);
  check("it says the service could not be reached", /could not reach the assistant service/.test(error?.textContent ?? ""));
  const link = error?.find((n) => n.tagName === "A");
  check("it links to the directory that still works", link?.getAttribute("href") === "/fallback");
  check("it does not claim the answer was unpublished", !/not published/i.test(error?.textContent ?? ""));
}

section("A suggestion chip asks its question");
{
  const { doc, calls } = run({ scriptSrc: CROSS_ORIGIN, apiBase: "https://api.example.org", reply: ROUTE_REPLY, inline: true });
  const root = doc.getElementById("cra-root");
  const chip = root.findAll((n) => n.hasClass("cra-chip"))[0];
  check("suggestions are offered", !!chip, `found ${root.findAll((n) => n.hasClass("cra-chip")).length}`);
  chip._fire("click");
  await flush();
  check("clicking one asks the API", calls.length === 1, `got ${calls.length}`);
  check("with that suggestion as the question", calls[0]?.init?.body?.includes("How do I apply?"), calls[0]?.init?.body);
}

console.log(
  failures === 0
    ? "\nwidget: all checks passed"
    : `\nwidget: ${failures} check(s) FAILED`
);
process.exit(failures === 0 ? 0 : 1);
