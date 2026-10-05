/* CU Route Assistant — embeddable widget.
 *
 * Design constraints this file honours:
 *   - One <script> tag, no build step, no framework at the host site.
 *   - Ships its own stylesheet. It used not to, and a host page that linked
 *     only this file got an unstyled wall of divs: the launcher sat in the top
 *     corner as unstyled text and the panel was invisible. A widget that needs
 *     the host to know what to link is not one tag, it is two.
 *   - Mounts inline when the host page provides an element with the id
 *     cra-root, and as a floating launcher otherwise. One file, both shapes, so
 *     the full page and a third-party embed cannot drift apart.
 *   - Degrades to a plain, actionable error if the API is unreachable: it does
 *     not vanish, because a chat box that silently disappears is read as a
 *     broken page.
 *   - Keyboard operable, screen-reader announced, no console errors when
 *     partially loaded.
 *   - Sends no analytics and sets no cookies.
 */
(function () {
  "use strict";

  var API = window.CU_ROUTE_API || "";
  var MOUNT_ID = "cu-route-assistant";
  var ROOT_ID = "cra-root";
  var STYLE_ID = "cra-widget-style";

  // Read while this file is still executing, because that is the only moment
  // document.currentScript refers to it. Reading it later — from the
  // DOMContentLoaded handler below, or after an async step — yields null, and
  // the stylesheet would then resolve against the host page's root, so an
  // embedder on a different origin would get a 404 for the CSS and an unstyled
  // widget.
  var SELF = document.currentScript && document.currentScript.src;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    if (text) node.textContent = text;
    return node;
  }

  // The widget's own stylesheet, loaded relative to this script so that an
  // embedder does not have to serve or reference a second file. Served with a
  // version query so a host page that pins an old copy still gets new CSS after
  // a deploy, without the host changing anything.
  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var base = SELF ? SELF.replace(/[?#].*$/, "").replace(/\/[^/]*$/, "/") : "";
    var link = el("link", {
      id: STYLE_ID,
      rel: "stylesheet",
      href: (base || "/") + "widget.css?v=3",
    });
    document.head.appendChild(link);
  }

  function inlineMarkdown(text) {
    // **bold** and _italic_ only. Applied to an already-escaped string by way of
    // text nodes below, never as HTML, so a model cannot inject markup.
    return String(text)
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/(^|[\s(])_([^_\n]+?)_(?=[\s.,;:!?)]|$)/g, "$1$2");
  }

  function renderRoute(route) {
    if (!route) return null;
    var box = el("div", { class: "cra-route" });
    box.appendChild(el("h4", { class: "cra-route-label" }, route.label));
    var dl = el("dl", { class: "cra-dl" });
    function row(term, value, href) {
      if (!value) return;
      dl.appendChild(el("dt", null, term));
      var dd = el("dd");
      if (href) {
        dd.appendChild(el("a", { href: href, target: "_blank", rel: "noopener" }, value));
      } else {
        dd.textContent = value;
      }
      dl.appendChild(dd);
    }
    row("Office", route.office);
    if (route.entry_point && route.entry_point.indexOf("http") === 0) {
      row("Where to go", route.entry_point, route.entry_point);
    }
    row("Email", route.email, route.email ? "mailto:" + route.email : null);
    row("Phone", route.phone);
    row("Also published", route.alternative_phone);
    row("WhatsApp", route.whatsapp);
    row("Opening hours", route.hours);
    row("Address", route.address);
    box.appendChild(dl);
    if (route.verification === "published_conflicting") {
      box.appendChild(
        el("p", { class: "cra-warn" },
          "The University publishes more than one contact detail for this. If the " +
          "first does not answer, try the second.")
      );
    }
    if (route.source_url) {
      box.appendChild(
        el("a", { class: "cra-src", href: route.source_url, target: "_blank", rel: "noopener" },
          "Where this came from")
      );
    }
    return box;
  }

  function renderCitations(citations) {
    if (!citations || !citations.length) return null;
    var wrap = el("div", { class: "cra-cites" });
    wrap.appendChild(el("h4", null, "Sources"));
    var list = el("ul");
    citations.forEach(function (c) {
      var li = el("li");
      li.appendChild(
        el("a", { href: c.source_url, target: "_blank", rel: "noopener" },
          c.source_title || c.source_url)
      );
      if (c.retrieved_at) {
        li.appendChild(el("span", { class: "cra-when" }, " (checked " + c.retrieved_at + ")"));
      }
      list.appendChild(li);
    });
    wrap.appendChild(list);
    return wrap;
  }

  // The status line above an answer, matched to why there is no answer. The
  // widget used to append "I did not find a published answer to that" for every
  // abstention, including to a safety refusal and a greeting, which is the same
  // false-claim bug the server-rendered page had.
  var STATUS = [
    ["unpublished topic", "Not published.",
     "The University does not publish this anywhere I can read, so I have not guessed."],
    ["safety gate", "I will not help with that.", "Here is why."],
    ["conversational", "", ""],
    ["outside domain", "Outside what I cover.",
     "I answer from the University's own published pages."],
    ["no route above threshold", "No published answer.",
     "I would rather tell you that than invent one."]
  ];

  function statusNode(data) {
    if (!data.abstained) return null;
    var reason = data.abstention_reason || "";
    for (var i = 0; i < STATUS.length; i++) {
      if (reason.indexOf(STATUS[i][0]) === 0) {
        if (!STATUS[i][1]) return null;
        return el("p", { class: "cra-status-note" },
                  STATUS[i][1] + " " + STATUS[i][2]);
      }
    }
    return el("p", { class: "cra-status-note" },
              "No published answer. I would rather tell you that than invent one.");
  }

  function bot(message) {
    // One renderer for every message the assistant sends, whether it is the
    // opening message or an answer from the API.
    //
    // A run of "- item" lines becomes a real <ul>. That mattered more than it
    // looks: the greeting is the first thing anyone sees, and it opens with a
    // list. A previous version had two nearly identical functions, and the one
    // used for API answers did not handle lists, so the greeting showed its own
    // dashes on screen — "− the right portal" — on the widget while the
    // server-rendered page had already been fixed.
    //
    // Answer text is authored by our own composer from cited sources, but it is
    // inserted as text nodes rather than HTML so that a model can never inject
    // markup into a host page.
    var node = el("div", { class: "cra-msg cra-bot" });
    var lines = String(message).split("\n");
    var list = null;
    lines.forEach(function (line) {
      if (!line.trim()) { list = null; return; }
      if (/^[-*]\s+/.test(line)) {
        if (!list) { list = el("ul", { class: "cra-list" }); node.appendChild(list); }
        list.appendChild(el("li", null, inlineMarkdown(line.replace(/^[-*]\s+/, ""))));
        return;
      }
      list = null;
      var p = el("p");
      var cleaned = inlineMarkdown(line.replace(/^#+\s*/, ""));
      var link = cleaned.match(/^(Where to go|Email):\s*(\S+)/);
      if (link) {
        p.appendChild(el("strong", null, link[1] + ": "));
        var href = link[1] === "Email" ? "mailto:" + link[2] : link[2];
        p.appendChild(el("a", { href: href, target: "_blank", rel: "noopener" }, link[2]));
      } else {
        p.textContent = cleaned;
      }
      node.appendChild(p);
    });
    return node;
  }

  function answerNode(data) {
    var block = el("div", { class: "cra-answer" });
    var status = statusNode(data);
    if (status) block.appendChild(status);
    block.appendChild(bot(data.answer));
    if (data.primary_route) {
      var r = renderRoute(data.primary_route);
      if (r) block.appendChild(r);
    }
    (data.also_consider || []).forEach(function (alt) {
      var wrap = el("details", { class: "cra-alt" });
      wrap.appendChild(el("summary", null, "Also consider: " + alt.label));
      var inner = renderRoute(alt);
      if (inner) wrap.appendChild(inner);
      block.appendChild(wrap);
    });
    var cites = renderCitations(data.citations);
    if (cites) block.appendChild(cites);
    return block;
  }

  function create() {
    ensureStyles();
    if (document.getElementById(MOUNT_ID)) return;

    var root = document.getElementById(ROOT_ID);
    var inline = !!root;

    var log = el("div", {
      class: "cra-log", role: "log", id: "cra-log",
      "aria-live": "polite", "aria-relevant": "additions"
    });
    var form = el("form", { class: "cra-form" });
    var input = el("input", {
      class: "cra-input", type: "text", name: "q", maxlength: "500",
      placeholder: "Ask about a portal, an office, or a phone number",
      "aria-label": "Your question", autocomplete: "off", required: "required"
    });
    var send = el("button", { class: "cra-send", type: "submit" }, "Ask");
    form.appendChild(input);
    form.appendChild(send);
    var status = el("p", { class: "cra-hint", role: "status" });

    var suggestions = el("div", { class: "cra-suggest" });
    [
      "How do I apply?",
      "Who do I email about a certificate programme?",
      "Where is the library?",
      "My portal is not working"
    ].forEach(function (q) {
      var chip = el("button", { class: "cra-chip", type: "button" }, q);
      chip.addEventListener("click", function () {
        input.value = q;
        form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(
          new Event("submit", { cancelable: true, bubbles: true }));
      });
      suggestions.appendChild(chip);
    });

    var head, close, launcher, panel;
    var submit = function (q) {
      if (!q) return;
      log.appendChild(el("div", { class: "cra-msg cra-user" }, q));
      input.value = "";
      status.textContent = "Looking…";
      send.disabled = true;
      log.scrollTop = log.scrollHeight;

      fetch(API + "/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q })
      })
        .then(function (r) {
          if (!r.ok) throw new Error("status " + r.status);
          return r.json();
        })
        .then(function (data) {
          log.appendChild(answerNode(data));
          status.textContent = "";
          log.scrollTop = log.scrollHeight;
        })
        .catch(function () {
          // Say what happened and what still works. Silently doing nothing, or
          // saying "no published answer", would both be lies.
          var err = el("div", { class: "cra-msg cra-bot cra-error" });
          err.appendChild(el("p", null,
            "I could not reach the assistant service just now, so I cannot answer " +
            "that. The contact directory below needs no service at all and is " +
            "still fully readable."));
          var link = el("a", { href: "/fallback" }, "Open the contact directory");
          err.appendChild(link);
          log.appendChild(err);
          status.textContent = "";
          log.scrollTop = log.scrollHeight;
        })
        .then(function () {
          send.disabled = false;
          input.focus();
        });
    };

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submit(input.value.trim());
    });

    if (inline) {
      root.classList.add("cra-root");
      head = el("div", { class: "cra-head" });
      head.appendChild(el("h2", { class: "cra-title" }, "Ask the University"));
      root.appendChild(head);
      root.appendChild(suggestions);
      root.appendChild(log);
      root.appendChild(status);
      root.appendChild(form);
      panel = root;
      launcher = null;
    } else {
      launcher = el("button", {
        id: MOUNT_ID, class: "cra-launcher", type: "button",
        "aria-expanded": "false", "aria-controls": "cra-panel"
      }, "Ask CU");
      panel = el("div", {
        id: "cra-panel", class: "cra-panel", role: "dialog",
        "aria-label": "Ask about Cosmopolitan University", hidden: "hidden"
      });
      head = el("div", { class: "cra-head" });
      head.appendChild(el("h3", { class: "cra-title" }, "Ask CU"));
      close = el("button", { class: "cra-close", type: "button", "aria-label": "Close" }, "×");
      head.appendChild(close);
      panel.appendChild(head);
      panel.appendChild(suggestions);
      panel.appendChild(log);
      panel.appendChild(status);
      panel.appendChild(form);

      function open() {
        panel.removeAttribute("hidden");
        launcher.setAttribute("aria-expanded", "true");
        input.focus();
      }
      function shut() {
        panel.setAttribute("hidden", "hidden");
        launcher.setAttribute("aria-expanded", "false");
        launcher.focus();
      }
      launcher.addEventListener("click", function () {
        panel.hasAttribute("hidden") ? open() : shut();
      });
      close.addEventListener("click", shut);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !panel.hasAttribute("hidden")) shut();
      });
      document.body.appendChild(launcher);
      document.body.appendChild(panel);
    }

    // A real opening message, not decoration. It says what the assistant is for
    // and that it will decline rather than guess, so the first thing a visitor
    // reads is the terms of the service.
    log.appendChild(bot(
      "Ask me where to apply, who to contact, or how to reach a service. I answer " +
      "from the University's own published pages and show you the source of every " +
      "answer, so you can check it.\n\n" +
      "I will not guess at fees, deadlines, or entry requirements — the University " +
      "does not publish them anywhere I can read, and a wrong figure from me could " +
      "cost you an application."
    ));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", create);
  } else {
    create();
  }
})();
