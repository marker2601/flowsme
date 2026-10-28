/* FlowSME website: progressive enhancement only. Everything on the page is readable without this file. */
(function () {
  "use strict";
  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (sel, el) { return (el || document).querySelector(sel); };
  var $$ = function (sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); };
  var hasIO = "IntersectionObserver" in window;

  // ---- light / dark (the same key the report pages use, so the choice carries over) -------------------
  var themeBtn = $("#theme");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("flowsme-theme", root.dataset.theme); } catch (e) {}
  });

  // ---- header: shadow once scrolled, menu on small screens --------------------------------------------
  var header = $(".site-header");
  var menuBtn = $("#menu-btn");
  var nav = $("#site-nav");
  var onScroll = function () { header.classList.toggle("scrolled", window.scrollY > 8); };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  function setMenu(open) {
    header.classList.toggle("menu-open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    document.body.classList.toggle("no-scroll", open);
  }
  menuBtn.addEventListener("click", function () { setMenu(menuBtn.getAttribute("aria-expanded") !== "true"); });
  nav.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && header.classList.contains("menu-open")) { setMenu(false); menuBtn.focus(); }
  });
  var wide = matchMedia("(min-width: 1024px)");
  var closeIfWide = function () { if (wide.matches) setMenu(false); };
  if (wide.addEventListener) wide.addEventListener("change", closeIfWide); else wide.addListener(closeIfWide);

  // ---- the navigation follows the section being read ---------------------------------------------------
  var navLinks = $$("a[href^='#']", nav);
  if (hasIO) {
    var bySection = {};
    navLinks.forEach(function (a) {
      var section = document.getElementById(a.getAttribute("href").slice(1));
      if (section) bySection[section.id] = a;
    });
    // every section is watched, so one without a link (the hero, Meta, consultants) clears the highlight
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        navLinks.forEach(function (a) { a.removeAttribute("aria-current"); });
        if (bySection[entry.target.id]) bySection[entry.target.id].setAttribute("aria-current", "location");
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    $$("main > section").forEach(function (section) { spy.observe(section); });
  }

  // ---- fade sections in as they arrive -----------------------------------------------------------------
  var reveal = $$("[data-reveal], .bars.animate");
  if (reduceMotion || !hasIO) reveal.forEach(function (el) { el.classList.add("in"); });
  else {
    var shown = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add("in"); shown.unobserve(entry.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    reveal.forEach(function (el) { shown.observe(el); });
  }

  // ---- tabs (WAI-ARIA tabs pattern: arrows, Home, End) -------------------------------------------------
  var tabSelectors = {};
  $$("[role='tablist']").forEach(function (list) {
    var tabs = $$("[role='tab']", list);
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (tab, i) {
      tabSelectors[tab.id] = function () { select(tab); };
      tab.addEventListener("click", function () { select(tab); });
      tab.addEventListener("keydown", function (e) {
        var n = tabs.length, j = null;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % n;
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + n) % n;
        else if (e.key === "Home") j = 0;
        else if (e.key === "End") j = n - 1;
        if (j !== null) { e.preventDefault(); select(tabs[j], true); }
      });
    });
    select(tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0]);
  });
  // links elsewhere on the page that open a tab of the sample ("See it in the sample")
  $$("[data-open-tab]").forEach(function (link) {
    link.addEventListener("click", function () { var open = tabSelectors[link.dataset.openTab]; if (open) open(); });
  });

  // ---- filter chips ------------------------------------------------------------------------------------
  $$("[data-filter-for]").forEach(function (group) {
    var target = document.getElementById(group.dataset.filterFor);
    var items = $$("[data-tags]", target);
    var count = $("[data-count-for='" + target.id + "']");
    var empty = $("[data-empty-for='" + target.id + "']");
    var buttons = $$("button[data-filter]", group);
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        var f = button.dataset.filter, visible = 0;
        buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b === button)); });
        items.forEach(function (item) {
          var match = f === "all" || (" " + item.dataset.tags + " ").indexOf(" " + f + " ") > -1;
          item.hidden = !match;
          if (match) visible++;
        });
        if (count) count.textContent = (visible === items.length ? "Showing all " + items.length : "Showing " + visible + " of " + items.length) + " " + count.dataset.noun;
        if (empty) empty.hidden = visible > 0;
        target.dispatchEvent(new CustomEvent("filtered", { detail: f }));
      });
    });
  });

  // ---- sorting -----------------------------------------------------------------------------------------
  $$("select[data-sort-for]").forEach(function (select) {
    var list = document.getElementById(select.dataset.sortFor);
    select.addEventListener("change", function () {
      var option = select.options[select.selectedIndex];
      var key = option.value, dir = option.dataset.dir === "asc" ? 1 : -1;
      $$("[data-tags]", list).sort(function (a, b) {
        return (parseFloat(a.dataset[key]) - parseFloat(b.dataset[key])) * dir;
      }).forEach(function (item) { list.appendChild(item); });
    });
  });

  // ---- one run: a timeline built from the step list, linked both ways ---------------------------------
  var runList = $("#run-steps");
  var timeline = $("#run-timeline");
  var runSteps = runList ? $$("li", runList).map(function (li) {
    return { n: li.dataset.step, app: li.dataset.app, appLabel: $(".app-chip", li).textContent.trim(),
             text: $(".desc", li).textContent.trim(), dur: parseFloat(li.dataset.dur), li: li };
  }) : [];
  if (timeline && runSteps.length) {
    runSteps.forEach(function (s) {
      var seg = document.createElement("button");
      seg.type = "button";
      seg.tabIndex = -1;
      seg.className = "seg app-" + s.app;
      seg.style.flexGrow = s.dur;
      seg.dataset.step = s.n;
      seg.title = "Step " + s.n + " · " + s.appLabel + " · " + s.dur + " s";
      timeline.appendChild(seg);
      s.seg = seg;
    });
    var highlight = function (n, scroll) {
      runSteps.forEach(function (s) {
        var on = s.n === n;
        s.seg.classList.toggle("on", on);
        s.li.classList.toggle("on", on);
        if (on && scroll && !s.li.hidden) runList.scrollTop = s.li.offsetTop - runList.clientHeight / 2 + s.li.offsetHeight / 2;
      });
    };
    timeline.addEventListener("mouseover", function (e) { var seg = e.target.closest(".seg"); if (seg) highlight(seg.dataset.step, true); });
    timeline.addEventListener("click", function (e) { var seg = e.target.closest(".seg"); if (seg) highlight(seg.dataset.step, true); });
    runList.addEventListener("mouseover", function (e) { var li = e.target.closest("li"); if (li) highlight(li.dataset.step, false); });
    runList.addEventListener("filtered", function (e) {
      runSteps.forEach(function (s) { s.seg.classList.toggle("dim", e.detail !== "all" && s.app !== e.detail); });
    });
  }

  // ---- hero: a recording turning into a report, from the same sample steps ----------------------------
  var demo = $("#demo");
  if (demo && runSteps.length) {
    var feed = $(".feed", demo), status = $(".status", demo), clock = $(".clock", demo), counter = $(".count", demo);
    var bar = $(".progress", demo), pauseBtn = $(".pause", demo);
    var segs = runSteps.map(function (s) {
      var seg = document.createElement("span");
      seg.className = "seg app-" + s.app;
      seg.style.flexGrow = s.dur;
      bar.appendChild(seg);
      return seg;
    });
    demo.classList.add("live");
    var LABEL = { rec: "Recording", ana: "Analysing", done: "Report ready" };
    var i = 0, elapsed = 0, timer = null, paused = false, onScreen = true;
    var fmt = function (sec) { var m = Math.floor(sec / 60), s = Math.floor(sec % 60); return m + ":" + (s < 10 ? "0" : "") + s; };
    var setPhase = function (p) { demo.dataset.phase = p; status.textContent = LABEL[p]; };
    var item = function (s, at) {
      var li = document.createElement("li");
      var chip = document.createElement("span"); chip.className = "app-chip app-" + s.app;
      chip.appendChild(document.createElement("i")); chip.appendChild(document.createTextNode(s.appLabel));
      var txt = document.createElement("span"); txt.className = "txt"; txt.textContent = s.text;
      var t = document.createElement("span"); t.className = "t"; t.textContent = fmt(at);
      li.appendChild(chip); li.appendChild(txt); li.appendChild(t);
      return li;
    };
    var reset = function () {
      i = 0; elapsed = 0; feed.textContent = "";
      segs.forEach(function (seg) { seg.classList.remove("on"); });
      clock.textContent = "0:00"; counter.textContent = "Step 0 of " + runSteps.length;
      setPhase("rec");
    };
    var addStep = function () {
      var s = runSteps[i];
      elapsed += s.dur;
      feed.appendChild(item(s, elapsed));
      while (feed.children.length > 6) feed.removeChild(feed.firstChild);
      segs[i].classList.add("on");
      i++;
      clock.textContent = fmt(elapsed);
      counter.textContent = "Step " + i + " of " + runSteps.length;
    };
    var schedule = function (ms) { clearTimeout(timer); timer = setTimeout(tick, ms); };
    var tick = function () {
      if (paused || !onScreen || document.hidden) return schedule(500);
      var phase = demo.dataset.phase;
      if (phase === "rec" && i < runSteps.length) { addStep(); schedule(i < runSteps.length ? 620 : 900); }
      else if (phase === "rec") { setPhase("ana"); schedule(1700); }
      else if (phase === "ana") { setPhase("done"); schedule(7500); }
      else { reset(); schedule(600); }
    };
    if (reduceMotion) {
      // a still picture of the finished state, no movement
      runSteps.forEach(function () { addStep(); });
      setPhase("done");
      pauseBtn.hidden = true;
    } else {
      reset();
      schedule(700);
      if (hasIO) new IntersectionObserver(function (entries) { onScreen = entries[0].isIntersecting; }).observe(demo);
      pauseBtn.addEventListener("click", function () {
        paused = !paused;
        pauseBtn.setAttribute("aria-pressed", String(paused));
        pauseBtn.setAttribute("aria-label", paused ? "Play the animation" : "Pause the animation");
        pauseBtn.innerHTML = paused
          ? '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>'
          : '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2h3v12H4zM9 2h3v12H9z" fill="currentColor"/></svg>';
      });
    }
  }

  // ---- savings calculator ------------------------------------------------------------------------------
  var calc = $("#calc");
  if (calc) {
    var money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
    var num = function (v, d) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }).format(v); };
    var fields = {};
    $$("[data-calc]", calc).forEach(function (input) {
      var key = input.dataset.calc;
      (fields[key] = fields[key] || []).push(input);
      input.addEventListener("input", function () {
        var v = parseFloat(input.value);
        if (isNaN(v)) return;
        fields[key].forEach(function (other) { if (other !== input) other.value = v; });
        update();
      });
    });
    var get = function (key) {
      var input = fields[key][0], v = parseFloat(input.value);
      var min = parseFloat(input.min), max = parseFloat(input.max);
      return Math.min(Math.max(isNaN(v) ? min : v, min), max);
    };
    var out = function (key, text) { $("[data-out='" + key + "']", calc).textContent = text; };
    var update = function () {
      var hoursMonth = get("minutes") * get("runs") * get("people") * get("share") / 100 / 60;
      var hoursYear = hoursMonth * 12;
      out("hoursMonth", num(hoursMonth, hoursMonth < 10 ? 1 : 0));
      out("hoursYear", num(hoursYear, 0) + " h");
      out("days", num(hoursYear / 8, 0));
      out("moneyYear", money.format(hoursYear * get("rate")));
    };
    update();
  }

  // ---- manager and employee views ----------------------------------------------------------------------
  var team = $("#team-demo");
  if (team) {
    var viewButtons = $$("[data-view-btn]", team);
    viewButtons.forEach(function (b) {
      b.addEventListener("click", function () {
        team.dataset.view = b.dataset.viewBtn;
        viewButtons.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      });
    });
  }
})();
