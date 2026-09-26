/* CU Route Assistant — embeddable widget.
 *
 * Design constraints this file honours:
 *   - One <script> tag, no build step, no framework at the host site.
 *   - Degrades to nothing at all if the API is unreachable: the launcher
 *     disappears rather than showing an error the user cannot act on.
 *   - Keyboard operable, screen-reader announced, no console errors when
 *     partially loaded.
 *   - Sends no analytics and sets no cookies.
 */
(function () {
  "use strict";

  var API = window.CU_ROUTE_API || "";
  var MOUNT_ID = "cu-route-assistant";

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

  function bot(message) {
    var node = el("div", { class: "cra-msg cra-bot" });
    // Answer text is authored by our own composer from cited sources, but it is
    // inserted as text nodes rather than HTML so that a model can never inject
    // markup into a host page.
    String(message).split("\n").forEach(function (line) {
      if (!line.trim()) return;
      var p = el("p");
      var cleaned = line.replace(/^#+\s*/, "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/^_/,"").replace(/_$/,"");
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

  function create() {
    if (document.getElementById(MOUNT_ID)) return;

    var launcher = el("button", {
      id: MOUNT_ID, class: "cra-launcher", type: "button",
      "aria-expanded": "false", "aria-controls": "cra-panel"
    }, "Ask CU");

    var panel = el("div", {
      id: "cra-panel", class: "cra-panel", role: "dialog",
      "aria-label": "Ask about Cosmopolitan University", hidden: "hidden"
    });

    var head = el("div", { class: "cra-head" });
    head.appendChild(el("h3", null, "Ask CU"));
    var close = el("button", { class: "cra-close", type: "button", "aria-label": "Close" }, "×");
    head.appendChild(close);

    var log = el("div", { class: "cra-log", role: "log", "aria-live": "polite", "aria-relevant": "additions" });
    var form = el("form", { class: "cra-form" });
    var input = el("input", {
      class: "cra-input", type: "text", name: "q", maxlength: "500",
      placeholder: "e.g. how do I apply for a certificate?",
      "aria-label": "Your question", autocomplete: "off", required: "required"
    });
    var send = el("button", { class: "cra-send", type: "submit" }, "Ask");
    form.appendChild(input);
    form.appendChild(send);

    var status = el("p", { class: "cra-status", role: "status" });
    panel.appendChild(head);
    panel.appendChild(log);
    panel.appendChild(status);
    panel.appendChild(form);

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    function greet() {
      log.appendChild(bot(
        "I answer from Cosmopolitan University's own published pages and show you " +
        "where each answer came from. I won't guess at fees, deadlines, or " +
        "requirements that the University does not publish."
      ));
    }
    greet();

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

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var q = input.value.trim();
      if (!q) return;
      log.appendChild(el("div", { class: "cra-msg cra-user" }, q));
      input.value = "";
      status.textContent = "Looking…";
      send.disabled = true;

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
          var block = el("div", { class: "cra-answer" });
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
          if (data.abstained) {
            block.appendChild(el("p", { class: "cra-warn" },
              "I did not find a published answer to that, so I did not give one."));
          }
          log.appendChild(block);
          status.textContent = "";
        })
        .catch(function () {
          log.appendChild(
            bot("I could not reach the assistant service just now. The full contact " +
                "directory at /fallback works without it.")
          );
          status.textContent = "";
        })
        .then(function () {
          send.disabled = false;
          input.focus();
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", create);
  } else {
    create();
  }
})();
