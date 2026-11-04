/*
 * Rendering layer only.
 *
 * - Every user-visible string comes from window.QUIZ_DATA / window.QUIZ_TEXTS.
 *   This file contains zero business copy of its own; a missing / empty field
 *   makes its whole block not render instead of showing a placeholder.
 * - No scoring logic lives here: window.SCORING.evaluate() owns every decision.
 * - No localStorage / sessionStorage / cookies / analytics / external resources.
 *   The alias and the answers exist only in these in-memory variables.
 */
(function () {
  "use strict";

  var DATA = window.QUIZ_DATA;
  var TEXTS = window.QUIZ_TEXTS;
  var S = window.SCORING;
  if (!DATA || !TEXTS || !S || typeof S.evaluate !== "function") return;

  var DIM_JOIN = "、"; /* ideographic comma: separator mandated for the {dims} slot */
  var ALIAS_MAX = 20;
  var API_MAIL = "/api/mail/send";

  /* ---------------- state (memory only, never persisted) ---------------- */

  var alias = "";
  var answers = {};
  var cursor = 0;
  var result = null;

  /* ---------------- helpers ---------------- */

  var QUESTIONS = DATA.questions || [];
  var OPTIONS = DATA.options || [];
  var DIMS = DATA.dims || [];
  var PER_DIM = (obj(DATA.rules) && DATA.rules.perDim) || 3;

  function obj(v) {
    return (v && typeof v === "object") ? v : null;
  }
  function str(v) {
    return (typeof v === "string" && v.trim() !== "") ? v : null;
  }
  function arr(v) {
    return (Array.isArray(v) && v.length > 0) ? v : null;
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function addIf(parent, node, text) {
    var s = str(text);
    if (s) { node.textContent = s; parent.appendChild(node); return node; }
    return null;
  }
  function fill(tpl, map) {
    return tpl.replace(/\{(\w+)\}/g, function (whole, key) {
      return Object.prototype.hasOwnProperty.call(map, key) ? String(map[key]) : "";
    });
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }
  function on(node, type, fn) {
    if (node) node.addEventListener(type, fn);
    return node;
  }
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function T(path) {
    var parts = path.split(".");
    var cur = TEXTS;
    for (var i = 0; i < parts.length; i++) {
      if (!cur || typeof cur !== "object") return null;
      cur = cur[parts[i]];
    }
    return (cur === undefined) ? null : cur;
  }

  function textOfDim(key, field) {
    var box = obj(T("dims"));
    var one = box ? obj(box[key]) : null;
    return one ? str(one[field]) : null;
  }

  function dimNameOf(key) {
    return textOfDim(key, "name") || (function () {
      for (var i = 0; i < DIMS.length; i++) {
        if (DIMS[i] && DIMS[i].key === key) return str(DIMS[i].name);
      }
      return null;
    })();
  }

  function questionById(id) {
    for (var i = 0; i < QUESTIONS.length; i++) {
      if (QUESTIONS[i] && QUESTIONS[i].id === id) return QUESTIONS[i];
    }
    return null;
  }

  function isAnswered(i) {
    var q = QUESTIONS[i];
    return !!q && (answers[q.id] === 0 || answers[q.id] === 1);
  }

  function firstUnanswered() {
    for (var i = 0; i < QUESTIONS.length; i++) if (!isAnswered(i)) return i;
    return QUESTIONS.length;
  }

  function answeredCount() {
    var n = 0;
    for (var i = 0; i < QUESTIONS.length; i++) if (isAnswered(i)) n++;
    return n;
  }

  function pct(n, d) {
    if (!d || d <= 0) return 0;
    return Math.max(0, Math.min(100, (n / d) * 100));
  }

  /* ---------------- views ---------------- */

  var views = {
    s0: document.getElementById("view-s0"),
    s1: document.getElementById("view-s1"),
    s2: document.getElementById("view-s2")
  };
  var inners = {
    s0: document.getElementById("s0-inner"),
    s1: document.getElementById("s1-inner"),
    s2: document.getElementById("s2-inner")
  };

  function show(name) {
    for (var k in views) {
      if (!Object.prototype.hasOwnProperty.call(views, k)) continue;
      if (!views[k]) continue;
      if (k === name) views[k].removeAttribute("hidden");
      else views[k].setAttribute("hidden", "");
    }
    if (window.scrollTo) window.scrollTo(0, 0);
  }

  /* ---------------- S0 lock screen ---------------- */

  function renderS0() {
    var host = clear(inners.s0);
    var meta = obj(DATA.meta) || {};
    var intro = obj(T("intro")) || {};
    var notice = obj(T("notice")) || {};
    var errors = obj(T("errors")) || {};

    if (str(meta.title)) document.title = meta.title;

    var heading = null;
    if (str(meta.title)) {
      heading = el("h1", "lock-title", meta.title);
      heading.id = "s0-h";
      host.appendChild(heading);
    }
    addIf(host, el("p", "lock-sub"), meta.subtitle);

    addIf(host, el("p", "hook"), intro.hook);
    addIf(host, el("p", "note"), intro.levelNote);

    var dimArr = arr(DIMS);
    if (dimArr) {
      host.appendChild(el("hr"));
      addIf(host, el("p", "meta"), intro.dimsTitle);
      var list = el("ul", "dim-list");
      for (var i = 0; i < dimArr.length; i++) {
        var d = dimArr[i];
        if (!d) continue;
        var name = str(d.name);
        var one = str(d.oneLiner);
        if (!name && !one) continue;
        var li = el("li");
        if (name) li.appendChild(el("span", "dim-name", name));
        if (one) li.appendChild(el("span", "dim-one", one));
        list.appendChild(li);
      }
      if (list.firstChild) host.appendChild(list);
    }

    host.appendChild(el("hr"));

    addIf(host, el("p", "note"), notice.honesty);
    addIf(host, el("p", "note-small"), notice.privacy);
    addIf(host, el("p", "note"), notice.lock);

    var form = el("form", "lock-form");
    form.setAttribute("novalidate", "");

    var input = null;
    var aliasBox = str(notice.aliasLabel);
    if (aliasBox) {
      var field = el("div", "field");
      input = el("input");
      input.type = "text";
      input.id = "alias-input";
      input.maxLength = ALIAS_MAX;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.value = alias;
      var lab = el("label", null, aliasBox);
      lab.setAttribute("for", "alias-input");
      field.appendChild(lab);
      field.appendChild(input);
      form.appendChild(field);
    }

    var box = null;
    if (str(notice.lockCheck)) {
      var check = el("label", "check");
      box = el("input");
      box.type = "checkbox";
      box.id = "lock-check";
      check.appendChild(box);
      check.appendChild(el("span", null, notice.lockCheck));
      form.appendChild(check);
    }

    var errNode = el("p", "err");
    errNode.setAttribute("role", "alert");
    errNode.hidden = true;
    form.appendChild(errNode);

    var go = null;
    if (str(intro.start)) {
      go = el("button", "btn", intro.start);
      go.type = "submit";
      form.appendChild(go);
    }

    on(form, "submit", function (ev) {
      ev.preventDefault();
      alias = input ? input.value.trim().slice(0, ALIAS_MAX) : "";
      var needMsg = str(errors.needCheck);
      if (box && !box.checked) {
        if (needMsg) { errNode.textContent = needMsg; errNode.hidden = false; }
        box.focus();
        return;
      }
      errNode.hidden = true;
      if (!QUESTIONS.length) return;
      cursor = Math.max(0, Math.min(cursor, QUESTIONS.length - 1));
      renderS1();
      show("s1");
    });

    host.appendChild(form);

    if (input) on(input, "input", function () { errNode.hidden = true; });
    if (box) on(box, "change", function () { errNode.hidden = true; });
  }

  /* ---------------- S1 question by question ---------------- */

  function renderS1() {
    var host = clear(inners.s1);
    var q = QUESTIONS[cursor];
    if (!q) return;

    var head = el("div", "q-head");
    var numNode = el("span", "q-num", String(cursor + 1));
    numNode.setAttribute("aria-hidden", "true");
    head.appendChild(numNode);
    head.appendChild(el("span", "q-count", (cursor + 1) + " / " + QUESTIONS.length));
    host.appendChild(head);

    var qText = el("p", "q-text", str(q.text) || "");
    qText.id = "q-current";
    host.appendChild(qText);

    addIf(host, el("p", "q-hint"), q.hint);

    var opts = el("div", "opts");
    opts.setAttribute("role", "group");
    opts.setAttribute("aria-labelledby", "q-current");
    for (var i = 0; i < OPTIONS.length; i++) {
      var o = OPTIONS[i];
      if (!o) continue;
      var label = str(o.label);
      if (!label) continue;
      var b = el("button", "opt", label);
      b.type = "button";
      b.setAttribute("data-value", String(o.value));
      on(b, "click", function () {
        answer(Number(this.getAttribute("data-value")));
      });
      opts.appendChild(b);
    }
    if (opts.firstChild) host.appendChild(opts);

    var foot = el("div", "q-foot");

    var back = el("button", "btn-back", String.fromCharCode(8592));
    back.type = "button";
    back.disabled = cursor === 0;
    back.setAttribute("aria-hidden", "false");
    on(back, "click", prev);
    foot.appendChild(back);

    var dots = el("ul", "dots");
    for (var j = 0; j < QUESTIONS.length; j++) {
      var li = el("li");
      var dot = el("button", "step");
      dot.type = "button";
      dot.setAttribute("data-index", String(j));
      dot.setAttribute("aria-label", String(j + 1));
      if (isAnswered(j)) dot.setAttribute("data-filled", "1");
      if (j === cursor) {
        dot.setAttribute("data-cur", "1");
        dot.setAttribute("aria-current", "step");
      }
      on(dot, "click", function () {
        goTo(Number(this.getAttribute("data-index")));
      });
      li.appendChild(dot);
      dots.appendChild(li);
    }
    foot.appendChild(dots);
    host.appendChild(foot);

    var firstBtn = opts.querySelector("button");
    if (firstBtn) firstBtn.focus();
  }

  function answer(value) {
    var q = QUESTIONS[cursor];
    if (!q || (value !== 0 && value !== 1)) return;
    answers[q.id] = value;
    if (cursor < QUESTIONS.length - 1) {
      cursor += 1;
      renderS1();
      return;
    }
    finish();
    if (!result) renderS1();
  }

  function prev() {
    if (cursor > 0) {
      cursor -= 1;
      renderS1();
    }
  }

  function goTo(i) {
    if (i < 0 || i >= QUESTIONS.length) return;
    if (i <= firstUnanswered()) {
      cursor = i;
      renderS1();
    }
  }

  /* ---------------- S2 result ---------------- */

  function finish() {
    try {
      result = S.evaluate(answers, DATA);
    } catch (e) {
      result = null;
    }
    if (!result) return;
    renderS2();
    show("s2");
  }

  function renderS2() {
    var host = clear(inners.s2);
    var R = obj(T("result")) || {};
    var V = obj(T("verdicts")) || {};
    var notice = obj(T("notice")) || {};
    var one = obj(V[result.verdict]) || {};
    var meta = obj(DATA.meta) || {};

    var main = el("section", "verdict");
    if (alias && str(R.titleFor)) {
      main.appendChild(el("p", "verdict-for", fill(R.titleFor, { alias: alias })));
    }
    var headEl = null;
    if (str(one.headline)) {
      headEl = el("h2", "verdict-head", one.headline);
      headEl.id = "verdict-heading";
      main.appendChild(headEl);
      if (str(meta.title)) {
        document.title = meta.title + " · " + one.headline;
      }
    }
    addIf(main, el("p", "verdict-lead"), one.lead);
    if (main.firstChild) host.appendChild(main);

    var bars = renderBars(R);
    if (bars) { host.appendChild(el("hr")); host.appendChild(bars); }

    var lights = renderTypes(R);
    if (lights) { host.appendChild(el("hr")); host.appendChild(lights); }

    var bl = renderBorderline(R);
    if (bl) { host.appendChild(el("hr")); host.appendChild(bl); }

    var kq = renderKeyQuestions(R);
    if (kq) { host.appendChild(el("hr")); host.appendChild(kq); }

    var rv = renderReview(R);
    if (rv) { host.appendChild(el("hr")); host.appendChild(rv); }

    var vid = renderVideo();
    if (vid) { host.appendChild(el("hr")); host.appendChild(vid); }

    var foot = el("div", "foot");
    addIf(foot, el("p"), notice.disclaimer);
    addIf(foot, el("p"), R.footer);
    if (foot.firstChild) host.appendChild(foot);

    var actions = el("div", "actions");
    if (str(R.again)) {
      var again = el("button", "btn-ghost", R.again);
      again.type = "button";
      on(again, "click", function () {
        answers = {};
        cursor = 0;
        result = null;
        renderS1();
        show("s1");
      });
      actions.appendChild(again);
    }
    if (str(R.copy)) {
      var cp = el("button", "btn-ghost", R.copy);
      cp.type = "button";
      var cpState = el("p", "ok");
      cpState.setAttribute("role", "status");
      cpState.hidden = true;
      on(cp, "click", function () {
        copyText(buildCopyText(), function (ok) {
          if (!ok) return;
          var msg = str(R.copied);
          if (msg) { cpState.textContent = msg; cpState.hidden = false; }
        });
      });
      actions.appendChild(cp);
      actions.appendChild(cpState);
    }
    if (actions.firstChild) host.appendChild(actions);

    var mail = renderMail(R);
    if (mail) host.appendChild(mail);

    var target = headEl || host.querySelector("button");
    if (target) {
      target.tabIndex = -1;
      target.focus();
    }
  }

  function renderBars(R) {
    var rank = arr(result.dimRank);
    var scores = obj(result.scores);
    if (!rank || !scores) return null;

    var wrap = el("section", "sec");
    addIf(wrap, el("h3", "sec-title"), R.dimsTitle);

    var grid = el("div", "bars");
    var any = false;
    for (var i = 0; i < rank.length; i++) {
      var key = rank[i];
      if (typeof scores[key] !== "number") continue;
      var name = dimNameOf(key);
      if (!name) continue;
      any = true;
      var score = scores[key];
      var lit = !!(result.present && result.present[key] === true);

      var row = el("div", "bar-row");

      var hd = el("div", "bar-head");
      hd.appendChild(el("span", "bar-name", name));
      var val = el("span", "bar-val");
      val.appendChild(el("b", null, String(score)));
      val.appendChild(document.createTextNode(" / " + PER_DIM));
      hd.appendChild(val);
      row.appendChild(hd);

      var track = el("div", "bar-track");
      var fillEl = el("div", "bar-fill");
      fillEl.setAttribute("data-on", lit ? "1" : "0");
      fillEl.style.width = pct(score, PER_DIM) + "%";
      // 宽度直接落到终值；过渡/动画交给 CSS（transition 在有差值的浏览器里才发生）。
      // 不用 requestAnimationFrame 起动画：rAF 可能被合帧、可能在最小化时挂起，
      // 结果会是「截图/自动化抓到的中间态」，而阈值线是同步定位的，二者对不上。
      track.appendChild(fillEl);

      var mark = el("div", "bar-mark");
      mark.style.left = pct(result.threshold, PER_DIM) + "%";
      mark.setAttribute("aria-hidden", "true");
      track.appendChild(mark);
      row.appendChild(track);

      addIf(row, el("p", "bar-long"), textOfDim(key, "long"));
      grid.appendChild(row);
    }
    if (!any) return null;
    wrap.appendChild(grid);
    addIf(wrap, el("p", "thresh-note"), R.thresholdNote);
    return wrap;
  }

  /* the eight real theory types: every TYPE_ORDER entry whose bits array exists
     (sprout is an answer-vector state, not one of the eight lights) */
  function realTypeKeys() {
    var order = arr(S.TYPE_ORDER);
    var meta = obj(S.TYPE_META);
    var out = [];
    if (!order || !meta) return out;
    for (var i = 0; i < order.length; i++) {
      var k = order[i];
      var m = obj(meta[k]);
      if (m && arr(m.bits)) out.push(k);
    }
    return out;
  }

  function renderTypes(R) {
    var box = obj(T("types"));
    if (!box) return null;

    var wrap = el("section", "sec");
    addIf(wrap, el("h3", "sec-title"), R.lightsTitle);

    var keys = realTypeKeys();
    if (keys.length) {
      var grid = el("div", "lights");
      for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        var entry = obj(box[key]);
        var name = entry ? str(entry.name) : null;
        if (!name) continue;
        var isCur = key === result.typeKey;
        var light = el("div", "light" + (isCur ? " is-current" : ""));
        if (isCur) {
          light.setAttribute("data-on", "1");
          light.setAttribute("aria-current", "true");
        }
        light.appendChild(el("span", "bulb"));
        light.appendChild(el("span", "light-name", name));
        grid.appendChild(light);
      }
      if (grid.firstChild) wrap.appendChild(grid);
    }

    var cur = obj(box[result.typeKey]);
    if (cur) {
      var det = el("div", "type-detail");
      addIf(det, el("p", "type-name"), cur.name);
      addIf(det, el("p", "type-headline"), cur.headline);
      var body = arr(cur.body);
      if (body) {
        var bwrap = el("div", "type-body");
        for (var j = 0; j < body.length; j++) {
          addIf(bwrap, el("p"), body[j]);
        }
        if (bwrap.firstChild) det.appendChild(bwrap);
      }
      if (str(cur.quote)) {
        var qn = el("div", "type-quote");
        qn.appendChild(el("span", null, cur.quote));
        if (str(cur.quoteTime)) {
          var tm = el("span", "q-time");
          tm.appendChild(el("time", null, cur.quoteTime));
          qn.appendChild(tm);
        }
        det.appendChild(qn);
      }
      if (det.firstChild) wrap.appendChild(det);
    }
    return wrap.firstChild ? wrap : null;
  }

  function renderBorderline(R) {
    var list = arr(result.borderlineDims);
    if (!list || !str(R.borderlineTitle) || !str(R.borderlineBody)) return null;
    var names = [];
    for (var i = 0; i < list.length; i++) {
      var n = dimNameOf(list[i]);
      if (n) names.push(n);
    }
    if (!names.length) return null;
    var box = el("div", "callout");
    box.appendChild(el("h3", null, R.borderlineTitle));
    box.appendChild(el("p", null, fill(R.borderlineBody, { dims: names.join(DIM_JOIN) })));
    return box;
  }

  function renderKeyQuestions(R) {
    var ids = arr(result.keyQuestionIds);
    if (!ids) return null;
    var box = el("div", "callout");
    addIf(box, el("h3"), R.keyTitle);
    addIf(box, el("p"), R.keyBody);
    var ul = el("ul");
    for (var i = 0; i < ids.length; i++) {
      var q = questionById(ids[i]);
      addIf(ul, el("li"), q && q.text);
    }
    if (ul.firstChild) box.appendChild(ul);
    return box.firstChild ? box : null;
  }

  function renderReview(R) {
    if (!str(R.reviewTitle)) return null;
    var wrap = el("section", "sec");
    wrap.appendChild(el("h3", "sec-title", R.reviewTitle));

    var nos = [];
    for (var i = 0; i < QUESTIONS.length; i++) {
      var q = QUESTIONS[i];
      if (q && answers[q.id] === 0) nos.push({ q: q, n: i + 1 });
    }
    if (nos.length) {
      addIf(wrap, el("p", "note-small"), R.reviewNo);
      var ul = el("ul", "review-list");
      for (var j = 0; j < nos.length; j++) {
        var text = str(nos[j].q.text);
        if (!text) continue;
        var li = el("li");
        li.appendChild(el("span", "review-no", String(nos[j].n)));
        li.appendChild(el("span", null, text));
        ul.appendChild(li);
      }
      if (ul.firstChild) wrap.appendChild(ul);
    } else {
      addIf(wrap, el("p", "note"), R.reviewAll);
    }
    return wrap.firstChild ? wrap : null;
  }

  function renderVideo() {
    var vid = obj(T("video"));
    if (!vid) return null;
    var paras = arr(vid.paragraphs);
    if (!paras) return null; /* empty paragraphs => the whole block does not render */

    var wrap = el("section", "sec");
    addIf(wrap, el("h3", "sec-title"), vid.title);
    addIf(wrap, el("p", "video-note"), vid.note);

    var any = false;
    for (var i = 0; i < paras.length; i++) {
      var p = obj(paras[i]);
      if (!p || !str(p.text)) continue;
      var row = el("div", "vp");
      if (str(p.time)) row.appendChild(el("time", null, p.time));
      else row.appendChild(el("span"));
      row.appendChild(el("p", null, p.text));
      wrap.appendChild(row);
      any = true;
    }
    return any ? wrap : null;
  }

  /* ---------------- copy to clipboard ---------------- */

  function buildCopyText() {
    var R = obj(T("result")) || {};
    var V = obj(T("verdicts")) || {};
    var box = obj(T("types")) || {};
    var notice = obj(T("notice")) || {};
    var one = obj(V[result.verdict]) || {};
    var cur = obj(box[result.typeKey]) || {};
    var lines = [];

    if (alias && str(R.titleFor)) lines.push(fill(R.titleFor, { alias: alias }));
    if (str(one.headline)) lines.push(one.headline);
    if (str(one.lead)) lines.push(one.lead);
    if (str(cur.name)) {
      lines.push(str(R.typeTitle) ? (R.typeTitle + ": " + cur.name) : cur.name);
    }
    if (str(cur.headline)) lines.push(cur.headline);

    var rank = arr(result.dimRank) || [];
    var scores = obj(result.scores) || {};
    for (var i = 0; i < rank.length; i++) {
      var key = rank[i];
      var name = dimNameOf(key);
      if (name && typeof scores[key] === "number") {
        lines.push(name + " " + scores[key] + "/" + PER_DIM);
      }
    }

    var bl = arr(result.borderlineDims);
    if (bl && str(R.borderlineTitle) && str(R.borderlineBody)) {
      var names = [];
      for (var j = 0; j < bl.length; j++) {
        var n = dimNameOf(bl[j]);
        if (n) names.push(n);
      }
      if (names.length) {
        lines.push(R.borderlineTitle + ": " +
          fill(R.borderlineBody, { dims: names.join(DIM_JOIN) }));
      }
    }

    if (str(notice.disclaimer)) lines.push(notice.disclaimer);
    else if (str(R.footer)) lines.push(R.footer);

    return lines.join("\n");
  }

  function copyText(text, done) {
    if (!text) { done(false); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { done(true); },
        function () { done(legacyCopy(text)); }
      );
      return;
    }
    done(legacyCopy(text));
  }

  function legacyCopy(text) {
    try {
      var ta = el("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = !!(document.execCommand && document.execCommand("copy"));
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  /* ---------------- optional mail ---------------- */

  function renderMail(R) {
    if (!str(R.emailTitle)) return null;
    var errors = obj(T("errors")) || {};

    var wrap = el("section", "mail");
    wrap.appendChild(el("h3", "sec-title", R.emailTitle));
    addIf(wrap, el("p", "video-note"), R.emailNote);

    var row = el("div", "mail-row");
    var field = el("div", "field");
    var input = el("input");
    input.type = "email";
    input.id = "mail-to";
    input.autocomplete = "off";
    input.spellcheck = false;
    if (str(R.emailPlaceholder)) input.placeholder = R.emailPlaceholder;
    field.appendChild(input);
    row.appendChild(field);

    var right = el("div");
    var btn = el("button", "btn", str(R.emailSend) || "");
    btn.type = "button";
    if (!str(R.emailSend)) btn.hidden = true;
    right.appendChild(btn);

    var msg = el("p", "err");
    msg.setAttribute("role", "alert");
    msg.hidden = true;
    right.appendChild(msg);
    row.appendChild(right);
    wrap.appendChild(row);

    var sendLabel = str(R.emailSend);
    var busyLabel = str(R.emailSending);

    function say(text) {
      if (!text) { msg.hidden = true; return; }
      msg.className = "err";
      msg.textContent = text;
      msg.hidden = false;
    }
    function sayOk(text) {
      if (!text) { msg.hidden = true; return; }
      msg.className = "ok";
      msg.textContent = text;
      msg.hidden = false;
    }

    on(input, "input", function () { msg.hidden = true; });
    on(btn, "click", function () {
      if (answeredCount() < QUESTIONS.length) return say(str(errors.needAllAnswers));
      var to = input.value.trim();
      if (!looksLikeEmail(to)) return say(str(errors.badEmail));
      btn.disabled = true;
      if (busyLabel) btn.textContent = busyLabel;
      postMail(to, function (code, reason) {
        btn.disabled = false;
        if (sendLabel) btn.textContent = sendLabel;
        if (code === "ok") return sayOk(str(R.emailOk));
        if (code === "bad_email") return say(str(errors.badEmail));
        if (code === "mail_disabled") return say(str(errors.mailDisabled));
        if (code === "rate_limited") return say(str(errors.rateLimited));
        if (code === "send_failed") {
          var tpl = str(errors.sendFailed);
          return say(tpl ? fill(tpl, { reason: str(reason) || "" }) : null);
        }
        if (code === "incomplete") return say(str(errors.needAllAnswers));
        return say(str(errors.network));
      });
    });

    return wrap;
  }

  function looksLikeEmail(v) {
    return /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/.test(v);
  }

  function mailPayload(address) {
    var a = {};
    for (var i = 0; i < QUESTIONS.length; i++) {
      a[QUESTIONS[i].id] = answers[QUESTIONS[i].id];
    }
    return { answers: a, alias: alias, to: address };
  }

  function postMail(address, cb) {
    if (!window.fetch) { cb("network"); return; }
    var body;
    try {
      body = JSON.stringify(mailPayload(address));
    } catch (e) {
      cb("network");
      return;
    }
    fetch(API_MAIL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      credentials: "omit"
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (json) {
        if (res.ok && json && json.ok === true) return cb("ok");
        if (json && typeof json.code === "string") {
          return cb(json.code, typeof json.reason === "string" ? json.reason : "");
        }
        cb("network");
      });
    }, function () {
      cb("network");
    });
  }

  /* ---------------- keyboard (S1 only) ---------------- */

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (!views.s1 || views.s1.hidden) return;
    var t = ev.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
    if (ev.key === "1" && OPTIONS[0]) { ev.preventDefault(); answer(OPTIONS[0].value); return; }
    if (ev.key === "2" && OPTIONS[1]) { ev.preventDefault(); answer(OPTIONS[1].value); return; }
    if (ev.key === "ArrowLeft") { ev.preventDefault(); prev(); return; }
    if (ev.key === "ArrowRight") {
      ev.preventDefault();
      if (answeredCount() === QUESTIONS.length && firstUnanswered() === QUESTIONS.length) finish();
      else goTo(firstUnanswered());
    }
  });

  /* ---------------- boot ---------------- */

  if (!views.s0 || !views.s1 || !views.s2 || !QUESTIONS.length) return;
  renderS0();
  show("s0");
})();
