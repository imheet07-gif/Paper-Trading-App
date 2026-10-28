// India Trader local app. Plain JavaScript, no libraries, no network beyond this computer's server.
(function () {
  "use strict";
  var TOKEN = document.querySelector('meta[name="app-token"]').content;
  var S = { state: null, scan: null, result: null, polling: null };
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function get(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); });
  }
  function post(kind, params) {
    return fetch("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json", "X-App-Token": TOKEN },
      body: JSON.stringify({ kind: kind, params: params || {} })
    }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); });
  }
  function pct(x, d) { return x == null || isNaN(x) ? "-" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d == null ? 1 : d) + "%"; }
  function pctPlain(x, d) { return x == null || isNaN(x) ? "-" : (x * 100).toFixed(d == null ? 1 : d) + "%"; }
  function num(x, d) { return x == null || isNaN(x) ? "-" : Number(x).toFixed(d == null ? 2 : d); }
  function groupIN(digits) {
    if (digits.length <= 3) return digits;
    var head = digits.slice(0, -3), tail = digits.slice(-3), parts = [];
    while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
    if (head) parts.unshift(head);
    return parts.join(",") + "," + tail;
  }
  function inr(x, dec) {
    if (x == null || x === "" || isNaN(Number(x))) return "-";
    var v = Number(x), neg = v < 0, d = dec == null ? 0 : dec;
    var s = Math.abs(v).toFixed(d), whole = s.split(".")[0], frac = s.split(".")[1];
    return (neg ? "-" : "") + "₹" + groupIN(whole) + (frac ? "." + frac : "");
  }
  function crore(x) {
    var v = Number(x);
    if (v >= 1e7) return "₹" + (v / 1e7).toFixed(v >= 1e9 ? 0 : 1) + " cr";
    if (v >= 1e5) return "₹" + (v / 1e5).toFixed(1) + " lakh";
    return inr(v);
  }
  function cls(x) { return x == null ? "" : x >= 0 ? "up" : "down"; }

  // ------------------------------------------------------------ tooltip
  var tip = $("tip");
  function showTip(text, ev) {
    tip.textContent = text; tip.style.display = "block";
    var x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + "px"; tip.style.top = (ev.clientY + 14) + "px";
  }
  function hideTip() { tip.style.display = "none"; }

  // ------------------------------------------------------------ charts (SVG)
  function niceTicks(lo, hi, n) {
    var span = hi - lo || 1, step = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / step;
    step *= err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    var out = [], v = Math.ceil(lo / step) * step;
    for (; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  // series: [{name, points: [[date, value]], cls: "1"|"2"|"neg", area: bool}]
  // Charts are drawn at the container's real width, so text keeps its size on a phone; they redraw on resize.
  var drawn = [];
  function remember(fn, args) {
    var el = args[0];
    drawn = drawn.filter(function (d) { return d.el !== el && document.body.contains(d.el); });
    drawn.push({ el: el, fn: fn, args: args });
  }
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      drawn = drawn.filter(function (d) { return document.body.contains(d.el); });
      drawn.slice().forEach(function (d) { d.fn.apply(null, d.args); });
    }, 150);
  });
  function widthOf(el) { return Math.max(300, Math.round(el.clientWidth || 760)); }
  function lineChart(el, series, opt) {
    remember(lineChart, [el, series, opt]);
    opt = opt || {};
    var W = widthOf(el), H = opt.height || 260, L = 56, R = 12, T = 10, B = 26;
    var all = [];
    series.forEach(function (s) { s.points.forEach(function (p) { all.push(p); }); });
    if (!all.length) { el.innerHTML = "<p class='muted'>No data.</p>"; return; }
    var dates = series[0].points.map(function (p) { return p[0]; });
    var t = function (d) { return Date.parse(d); };
    var x0 = t(dates[0]), x1 = t(dates[dates.length - 1]);
    var vals = all.map(function (p) { return p[1]; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (opt.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    var log = opt.log && lo > 0;
    var f = log ? Math.log : function (v) { return v; };
    var y0 = f(lo), y1 = f(hi);
    if (y1 === y0) { y1 += 1; }
    var X = function (d) { return L + (t(d) - x0) / (x1 - x0 || 1) * (W - L - R); };
    var Y = function (v) { return T + (1 - (f(v) - y0) / (y1 - y0)) * (H - T - B); };
    var svg = ["<svg class='chart' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='" + esc(opt.label || "chart") + "'>"];
    var ticks = log ? niceTicks(lo, hi, 4).filter(function (v) { return v > 0; }) : niceTicks(lo, hi, 4);
    ticks.forEach(function (v) {
      var y = Y(v);
      if (y < T - 1 || y > H - B + 1) return;
      svg.push("<line class='" + (v === 0 && opt.zero ? "base" : "grid") + "' x1='" + L + "' x2='" + (W - R) + "' y1='" + y + "' y2='" + y + "'/>");
      svg.push("<text x='" + (L - 6) + "' y='" + (y + 4) + "' text-anchor='end'>" + esc((opt.yfmt || num)(v)) + "</text>");
    });
    var y0d = new Date(x0).getFullYear(), y1d = new Date(x1).getFullYear();
    var every = Math.max(1, Math.ceil((y1d - y0d + 1) / Math.max(2, Math.floor((W - L - R) / 70))));
    for (var yr = y0d; yr <= y1d; yr += every) {
      var dx = X(yr + "-01-01");
      if (dx < L || dx > W - R) continue;
      svg.push("<text x='" + dx + "' y='" + (H - 8) + "' text-anchor='middle'>" + yr + "</text>");
    }
    series.forEach(function (s) {
      var d = s.points.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); }).join("");
      if (s.area) {
        svg.push("<path class='fneg' d='" + d + "L" + X(s.points[s.points.length - 1][0]).toFixed(1) + " " + Y(0) + "L" + X(s.points[0][0]).toFixed(1) + " " + Y(0) + "Z'/>");
      }
      svg.push("<path class='s" + s.cls + "' d='" + d + "' fill='none' stroke-width='2' stroke-linejoin='round'/>");
    });
    svg.push("<line class='cross' id='cx' x1='0' x2='0' y1='" + T + "' y2='" + (H - B) + "' visibility='hidden'/>");
    series.forEach(function (s, i) { svg.push("<circle class='dot" + (s.cls === "2" ? "2" : "1") + "' r='4' visibility='hidden' data-i='" + i + "'/>"); });
    svg.push("<rect x='" + L + "' y='" + T + "' width='" + (W - L - R) + "' height='" + (H - T - B) + "' fill='transparent' class='hit'/></svg>");
    var legend = series.length > 1 ? "<div class='legend'>" + series.map(function (s) {
      return "<span><i style='background:var(--" + (s.cls === "neg" ? "neg" : "s" + s.cls) + ")'></i>" + esc(s.name) + "</span>";
    }).join("") + "</div>" : "";
    el.innerHTML = legend + svg.join("");
    var root = el.querySelector("svg"), hit = root.querySelector(".hit"), cx = root.querySelector("#cx");
    var dots = root.querySelectorAll("circle");
    hit.addEventListener("mousemove", function (ev) {
      var box = root.getBoundingClientRect(), px = (ev.clientX - box.left) / box.width * W;
      var target = x0 + (px - L) / (W - L - R) * (x1 - x0), best = 0, bd = Infinity;
      dates.forEach(function (d, i) { var dd = Math.abs(t(d) - target); if (dd < bd) { bd = dd; best = i; } });
      var d = dates[best], xs = X(d);
      cx.setAttribute("x1", xs); cx.setAttribute("x2", xs); cx.setAttribute("visibility", "visible");
      var lines = [d];
      series.forEach(function (s, i) {
        var p = s.points[Math.min(best, s.points.length - 1)];
        dots[i].setAttribute("cx", X(p[0])); dots[i].setAttribute("cy", Y(p[1])); dots[i].setAttribute("visibility", "visible");
        lines.push(s.name + ": " + (opt.tipfmt || opt.yfmt || num)(p[1]));
      });
      showTip(lines.join("\n"), ev);
    });
    hit.addEventListener("mouseleave", function () {
      hideTip(); cx.setAttribute("visibility", "hidden");
      dots.forEach(function (c) { c.setAttribute("visibility", "hidden"); });
    });
  }
  // groups: [{label, values: [a, b]}], names: ["Strategy", "Universe"]
  function barChart(el, groups, names, opt) {
    remember(barChart, [el, groups, names, opt]);
    opt = opt || {};
    var W = widthOf(el), H = opt.height || 240, L = 48, R = 8, T = 18, B = 26;
    var vals = [];
    groups.forEach(function (g) { g.values.forEach(function (v) { if (v != null) vals.push(v); }); });
    var lo = Math.min(0, Math.min.apply(null, vals)), hi = Math.max(0, Math.max.apply(null, vals));
    var Y = function (v) { return T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B); };
    var slot = (W - L - R) / groups.length, bw = Math.min(22, slot / (names.length + 1.2));
    var svg = ["<svg class='chart' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='" + esc(opt.label || "bars") + "'>"];
    niceTicks(lo, hi, 4).forEach(function (v) {
      var y = Y(v);
      svg.push("<line class='" + (v === 0 ? "base" : "grid") + "' x1='" + L + "' x2='" + (W - R) + "' y1='" + y + "' y2='" + y + "'/>");
      svg.push("<text x='" + (L - 6) + "' y='" + (y + 4) + "' text-anchor='end'>" + esc(pctPlain(v, 0)) + "</text>");
    });
    groups.forEach(function (g, i) {
      var cx = L + slot * i + slot / 2, start = cx - (bw * names.length + 2 * (names.length - 1)) / 2;
      g.values.forEach(function (v, k) {
        if (v == null) return;
        var x = start + k * (bw + 2), y = Math.min(Y(v), Y(0)), h = Math.max(Math.abs(Y(v) - Y(0)), 1.5);
        svg.push("<rect class='f" + (k + 1) + " bar' x='" + x.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + bw.toFixed(1) + "' height='" + h.toFixed(1) + "' rx='3' data-tip='" + esc(g.label + ", " + names[k] + ": " + pct(v)) + "'/>");
      });
      var every = Math.max(1, Math.ceil(groups.length / Math.max(2, Math.floor((W - L - R) / 44))));
      if (i % every === 0) svg.push("<text x='" + cx + "' y='" + (H - 8) + "' text-anchor='middle'>" + esc(g.label) + "</text>");
    });
    svg.push("</svg>");
    el.innerHTML = "<div class='legend'>" + names.map(function (n, k) {
      return "<span><i style='background:var(--s" + (k + 1) + ")'></i>" + esc(n) + "</span>";
    }).join("") + "</div>" + svg.join("");
    el.querySelectorAll("rect.bar").forEach(function (r) {
      r.addEventListener("mousemove", function (ev) { showTip(r.getAttribute("data-tip"), ev); });
      r.addEventListener("mouseleave", hideTip);
    });
  }

  // ------------------------------------------------------------ tabs, theme, status, jobs
  document.querySelectorAll("#tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll("#tabs button").forEach(function (x) { x.classList.toggle("on", x === b); });
      ["today", "ask", "lab", "paper", "runs"].forEach(function (t) { $("tab-" + t).hidden = t !== b.dataset.tab; });
      if (b.dataset.tab === "ask") renderAsk();
      if (b.dataset.tab === "runs") renderRuns();
      if (b.dataset.tab === "paper") renderPaper();
      if (b.dataset.tab === "lab") renderLabList();
    });
  });
  $("theme").addEventListener("click", function () {
    var r = document.documentElement, cur = r.getAttribute("data-theme");
    var dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    r.setAttribute("data-theme", dark ? "light" : "dark");
    try { localStorage.setItem("theme", r.getAttribute("data-theme")); } catch (e) {}
  });
  try { var th = localStorage.getItem("theme"); if (th) document.documentElement.setAttribute("data-theme", th); } catch (e) {}

  function renderStatus() {
    var st = S.state, el = $("status");
    var data = st.data_ends ? "Market data to <b>" + esc(st.data_ends) + "</b>" : "No market data yet";
    var fresh = st.stale == null ? "<span class='pill warn'>" + esc(st.calendar_error || "calendar unknown") + "</span>"
      : st.stale ? "<span class='pill warn'>needs update: " + esc(st.expected) + " should be published</span>"
        : "<span class='pill ok'>up to date</span>";
    var opts = st.strategies.map(function (s) { return "<option value='" + esc(s.name) + "'>" + esc(s.name) + "</option>"; }).join("");
    el.innerHTML = "<span>" + data + "</span>" + fresh +
      "<span class='row' style='margin-left:auto'><button class='secondary' id='btn-update'>Update data</button>" +
      "<select id='scan-strategy' aria-label='Strategy'>" + opts + "</select>" +
      "<label class='small muted'>paper capital <input type='number' id='scan-capital' placeholder='first run' min='10000' step='10000'></label>" +
      "<button class='primary' id='btn-scan'>Run scan</button></span>";
    $("btn-update").onclick = function () { startJob("update", {}, "Update market data"); };
    $("btn-scan").onclick = function () {
      var cap = $("scan-capital").value;
      startJob("scan", { strategy: $("scan-strategy").value, capital: cap || undefined }, "Run scan");
    };
    $("mydash").hidden = !st.has_dashboard;
  }

  function startJob(kind, params, title, onDone) {
    $("job").hidden = false; $("job-title").textContent = title; $("job-state").textContent = "starting";
    $("job-state").className = "pill"; $("job-log").textContent = "";
    setBusy(true);
    post(kind, params).then(function (j) { poll(j.id, onDone); }).catch(function (e) {
      $("job-state").textContent = "not started"; $("job-state").className = "pill bad";
      $("job-log").textContent = e.message; setBusy(false);
    });
  }
  function setBusy(b) { document.querySelectorAll("button.primary,#btn-update").forEach(function (x) { x.disabled = b; }); }
  function poll(id, onDone) {
    clearTimeout(S.polling);
    get("/api/jobs/" + id).then(function (j) {
      var log = $("job-log"); log.textContent = j.log.join("\n"); log.scrollTop = log.scrollHeight;
      $("job-state").textContent = j.state; $("job-state").className = "pill" + (j.state === "failed" ? " bad" : j.state === "done" ? " ok" : "");
      if (j.state === "running") { S.polling = setTimeout(function () { poll(id, onDone); }, 1200); return; }
      setBusy(false);
      refresh().then(function () { if (j.state === "done" && onDone) onDone(j); });
    });
  }

  // ------------------------------------------------------------ Ask (the trade finder)
  var askBuilt = false;
  function renderAsk() {
    var el = $("tab-ask");
    if (!askBuilt) {
      el.innerHTML = "<div class='card'><h2>Ask the trade finder</h2>" +
        "<p class='muted small'>The highest ranked trades by the one tested signal, after its filters, sized to a risk level. " +
        "An order sheet to read and enter by hand, never an order.</p><div class='form'>" +
        "<label>Amount (₹) <input type='number' id='ask-amount' min='1000' step='1000' value='50000' style='width:130px'></label>" +
        "<label>Risk <select id='ask-risk'><option value='low'>low: 5 stocks, lose at most 0.5% per stop</option>" +
        "<option value='medium' selected>medium: 3 stocks, 1% per stop</option><option value='high'>high: 1 stock, 2% per stop</option></select></label>" +
        "<label>Also check <input type='text' id='ask-stock' maxlength='20' placeholder='a symbol, e.g. TCS' style='width:150px'></label>" +
        "<div><button class='primary' id='btn-ask'>Ask</button></div></div></div><div id='ask-out'></div>";
      $("btn-ask").onclick = function () {
        startJob("advise", { amount: $("ask-amount").value, risk: $("ask-risk").value, stock: $("ask-stock").value.trim() || undefined },
          "Trade finder", loadAdvice);
      };
      askBuilt = true;
    }
    loadAdvice();
  }
  function loadAdvice() { get("/api/advice").then(renderAdvice); }
  function li(items) { return items && items.length ? "<ul>" + items.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : ""; }
  function pctText(x) { return x == null ? "-" : (Number(x) * 100).toFixed(1) + "%"; }
  function renderAdvice(a) {
    var el = $("ask-out");
    if (!el) return;
    if (!a || !a.date) { el.innerHTML = "<div class='card muted'>No answer yet. Ask above.</div>"; return; }
    var h = ["<div class='card'><div class='row spread'><h2>" + inr(a.amount) + ", " + esc(a.risk) + " risk</h2>" +
      "<span class='muted small'>data to " + esc(a.date) + "; review on " + esc(a.review_date || "the next rebalance") + "</span></div>" +
      "<p class='small muted'>" + esc(a.evidence) + "</p>"];
    if (a.fallback && a.risk_asked && a.risk !== a.risk_asked) h.push("<div class='note'>Asked for " + esc(a.risk_asked) + " risk: " + esc(a.fallback) + ".</div>");
    var lr = a.level_record || {};
    [["dev", "2013 to 2020"], ["validation", "2021 to 2023"]].forEach(function (pp) {
      var x = lr[pp[0]];
      if (x) h.push("<p class='small'>" + esc(a.risk) + " risk, " + pp[1] + ": " + pctText(x.cagr) + " a year (average stock " + pctText(x.universe_cagr) +
        "), worst drop " + pctText(x.max_drawdown) + ", worst month " + pctText(x.worst_month) + " (" + inr(x.worst_month_rupees) + " on this amount)</p>");
    });
    if (a.answer) h.push("<div class='note bad'>" + esc(a.answer) + "</div>");
    if (a.note) h.push("<div class='note'>" + esc(a.note) + "</div>");
    h.push("</div>");
    (a.picks || []).forEach(function (c, i) {
      var pl = c.plan || {};
      h.push("<div class='card'><div class='row spread'><h2>" + (i + 1) + ". " + esc(c.symbol) + " <span class='muted small'>" + esc(c.sector || "") + "</span></h2>" +
        "<span class='pill'>rank " + c.rank + " of " + c.of + "</span></div>" +
        "<p><b>" + esc(pl.order) + "</b>, about " + inr(pl.cost) + "</p>" +
        "<div class='kpis'>" + kpi("Stop", inr(pl.stop, 2), "lose " + inr(pl.loss_at_stop, 2) + " there") +
        kpi("Target", inr(pl.target, 2), "gain " + inr(pl.gain_at_target, 2) + " there") +
        kpi("Charges", pl.round_trip_pct ? pl.round_trip_pct + "%" : "-", "round trip") + "</div>" +
        "<p class='small muted'>" + esc(pl.exits_label || "") + "</p>" +
        (c.bucket ? "<p class='small'>" + esc(c.bucket.label) + " of the ranking: beat the average stock by " +
          ["dev", "validation"].filter(function (k) { return c.bucket[k] != null; }).map(function (k) { return k + " " + c.bucket[k] + "% a month"; }).join(", ") + "</p>" : "") +
        "<p class='small'>12-1 return " + pctPlain(c.parts.return_12_1) + ", 1-year volatility " + pctPlain(c.parts.volatility_1y) +
        ", 1 month " + pct(c.recent.return_1m) + ", " + pctPlain(c.recent.from_52w_high) + " from its 52-week high</p>" +
        (c.flags && c.flags.length ? "<h3>Flags</h3>" + li(c.flags) : "") +
        (c.results ? "<p class='small muted'>Untested context: results " + esc(c.results.quarter) + ", revenue " + esc(c.results.revenue_growth_pct || "?") +
          "%, profit " + esc(c.results.profit_growth_pct || "?") + "% against a year earlier</p>" : "") + "</div>");
    });
    if (a.low_profile && a.low_profile.length) {
      h.push("<div class='card'><h2>The low-risk alternative</h2>" + li(a.low_profile.map(function (x) { return x.order + " (lose " + inr(x.loss_at_stop, 2) + " at the stop)"; })) + "</div>");
    }
    if (a.dropped && a.dropped.length) {
      h.push("<div class='card'><h2>Passed over</h2>" + li(a.dropped.slice(0, 10).map(function (d) { return d.symbol + " (rank " + d.rank + "): " + d.why.join("; "); })) + "</div>");
    }
    if (a.stock) {
      var s = a.stock;
      h.push("<div class='card'><h2>" + esc(s.symbol) + "</h2><p>" + esc(s.verdict) + "</p>" + (s.filters && s.filters.length ? li(s.filters) : "") + "</div>");
    }
    h.push("<div class='card small'><b>Not available yet:</b> " + esc((a.pending || []).join("; ")) +
      ".<br>Order sheet only: nothing here places an order. Not financial advice.</div>");
    el.innerHTML = h.join("");
  }

  // ------------------------------------------------------------ Today
  function renderToday() {
    var sc = S.scan, el = $("tab-today");
    if (!sc || !sc.date) {
      el.innerHTML = "<div class='card'><h2>No scan yet</h2><p class='muted'>Update the market data, then run the scan. " +
        "The first run needs a paper capital (for example 1000000 for Rs 10 lakh).</p></div>";
      return;
    }
    var start = Number(sc.capital), nav = Number(sc.nav);
    var html = "<div class='kpis'>" +
      kpi("Scan date", esc(sc.date), esc(sc.strategy)) +
      kpi("Paper portfolio", inr(nav), pct(nav / start - 1) + " since " + esc(sc.started)) +
      kpi("Eligible today", sc.universe, "stocks pass the filters") +
      kpi(sc.rebalance ? "Rebalance" : "Next rebalance", sc.rebalance ? "today" : esc(sc.next_rebalance),
        sc.rebalance ? "orders below, for the next open" : "no orders until then") + "</div>";
    html += "<div class='note'><b>Status:</b> " + esc(sc.status) + "</div>";
    if (sc.orders && sc.orders.length) {
      html += "<div class='card'><div class='row spread'><h2>Order sheet for the next open</h2><a href='/files/scan/" +
        esc(sc.date) + "-orders.csv' download>Download CSV</a></div><p class='muted small'>Day limit orders, sells first. " +
        "Enter by hand if you follow the strategy; the paper ledger fills them at the next open.</p><div class='tablewrap'><table><thead><tr>" +
        "<th>Side</th><th>Stock</th><th class='n'>Qty</th><th class='n'>Limit</th><th class='n'>Value</th><th>Why</th></tr></thead><tbody>" +
        sc.orders.slice().sort(function (a, b) { return a.side === "sell" ? -1 : 1; }).map(function (o) {
          return "<tr><td>" + esc(o.side.toUpperCase()) + "</td><td>" + esc(o.symbol) + "</td><td class='n'>" + o.qty +
            "</td><td class='n'>" + inr(o.limit, 2) + "</td><td class='n'>" + inr(Number(o.limit) * o.qty) + "</td><td>" + esc(o.reason) + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }
    html += "<div class='card'><h2>Today's top " + sc.cards.length + "</h2><p class='muted small'>Click a row for its evidence. " +
      "Red flags are for your check; they do not change the rank.</p><div class='tablewrap'><table><thead><tr><th class='n'>#</th>" +
      "<th>Stock</th><th>Sector</th><th class='n'>Score</th><th class='n'>12-1 return</th><th class='n'>Volatility</th>" +
      "<th class='n'>6 months</th><th class='n'>From high</th><th class='n'>Traded / day</th><th>Flags</th></tr></thead><tbody>";
    sc.cards.forEach(function (c, i) {
      html += "<tr class='click' data-i='" + i + "'><td class='n'>" + c.rank + "</td><td><b>" + esc(c.symbol) + "</b></td><td>" + esc(c.sector) +
        "</td><td class='n'>" + num(c.score) + "</td><td class='n'>" + pct(c.ret_12_1, 0) + "</td><td class='n'>" + pctPlain(c.vol, 0) +
        "</td><td class='n'>" + pct(c.ret_6m, 0) + "</td><td class='n'>" + pct(c.from_high, 0) + "</td><td class='n'>" + crore(c.median_value) +
        "</td><td>" + (c.flags.length ? "<span class='pill warn'>" + c.flags.length + "</span>" : "") + "</td></tr>" +
        "<tr class='detail' id='d" + i + "' hidden><td colspan='10'>" + cardDetail(c) + "</td></tr>";
    });
    html += "</tbody></table></div></div>";
    var m = sc.method;
    if (m) {
      html += "<div class='card'><h2>How this ranking works</h2><p><b>Score.</b> " + esc(m.score) + "</p><p><b>Who is eligible.</b></p><ul>" +
        m.filters.map(function (f) { return "<li>" + esc(f) + "</li>"; }).join("") + "</ul><p><b>Portfolio.</b> " + esc(m.portfolio) +
        "</p><p>" + esc(m.flags) + "</p>" + (m.example ? "<p class='muted'>" + esc(m.example) + "</p>" : "") + "</div>";
    }
    html += "<div class='card'><h2>Evidence behind this strategy</h2><div class='tablewrap'><table><tbody>" +
      Object.keys(sc.evidence || {}).map(function (k) { return "<tr><td>" + esc(k) + "</td><td style='white-space:normal'>" + esc(sc.evidence[k]) + "</td></tr>"; }).join("") +
      "</tbody></table></div><p class='small'><a href='/files/scan/" + esc(sc.date) + ".html' target='_blank'>Open the full scan page</a></p></div>";
    el.innerHTML = html;
    el.querySelectorAll("tr.click").forEach(function (r) {
      r.addEventListener("click", function () { var d = $("d" + r.dataset.i); d.hidden = !d.hidden; });
    });
  }
  function kpi(k, v, s) { return "<div class='kpi'><div class='k'>" + k + "</div><div class='v'>" + v + "</div><div class='s'>" + s + "</div></div>"; }
  function cardDetail(c) {
    return "<div class='grid2'><div><b>Inputs</b><ul><li>Close " + inr(c.close, 2) + "; 1 month " + pct(c.ret_1m) + "</li>" +
      "<li>Delivery " + pctPlain(c.deliv_20, 0) + " over 20 days against " + pctPlain(c.deliv_250, 0) + " over a year</li>" +
      "<li>Days locked at a circuit in the last 20: " + c.locked_days + "</li>" +
      "<li>Corporate actions in the past year: " + (c.actions.length ? c.actions.map(function (a) { return esc(a[0] + " " + a[1]); }).join("; ") : "none") + "</li>" +
      "<li>Ranked " + c.rank + " of " + c.eligible + " scored</li></ul></div><div><b>Red flags</b><ul>" +
      (c.flags.length ? c.flags.map(function (f) { return "<li>" + esc(f) + "</li>"; }).join("") : "<li class='muted'>none</li>") + "</ul></div></div>";
  }

  // ------------------------------------------------------------ Strategy lab
  function renderLab() {
    var st = S.state, el = $("tab-lab");
    var sigOpts = st.signals.map(function (s) { return "<option>" + esc(s) + "</option>"; }).join("");
    var regOpts = st.regimes.map(function (s) { return "<option>" + esc(s) + "</option>"; }).join("");
    var weights = st.signals.filter(function (s) { return s !== "momentum_lowvol" && (st.event_signals || []).indexOf(s) < 0; }).map(function (s) {
      return "<label>" + esc(s) + " <input type='number' min='0' max='100' step='1' value='" + (s === "momentum_quality" ? 1 : 0) + "' data-w='" + esc(s) + "'></label>";
    }).join("");
    el.innerHTML = "<div class='card'><h2>Test a strategy</h2><p class='muted small'>Every test is logged and counted. " +
      "The more variations tried, the more a good result can be luck, and the deflated Sharpe below says how much. " +
      "Build on dev (2013 to 2020); spend a validation look (2021 to 2023) only on a finished idea. The 2024 onward holdout is not available here.</p>" +
      "<div class='form'><div class='full row'><label><input type='radio' name='mode' value='single' checked> One signal</label>" +
      "<label><input type='radio' name='mode' value='matrix'> Scoring matrix (weights fixed before testing)</label></div>" +
      "<label id='single-box'>Signal <select id='lab-signal'>" + sigOpts + "</select></label>" +
      "<div id='matrix-box' class='full' hidden><div class='weights'>" + weights + "</div><p class='muted small'>" +
      "Each stock's score is the weighted average of its percentile rank on each signal. Choose weights for a reason, not by trying until something works.</p></div>" +
      "<label>Stocks held <input type='number' id='lab-top' value='20' min='5' max='50'></label>" +
      "<label>Market timing <select id='lab-regime'>" + regOpts + "</select></label>" +
      "<label>Slippage, bps per side <input type='number' id='lab-slip' value='15' min='0' max='200'></label>" +
      "<div class='row'><label><input type='radio' name='period' value='dev' checked> dev 2013-2020</label>" +
      "<label><input type='radio' name='period' value='dev_2016'> dev 2016-2020 (insider data starts 2016)</label>" +
      "<label><input type='radio' name='period' value='validation'> validation 2021-2023</label></div>" +
      "<label class='full'>Why this test <input type='text' id='lab-note' maxlength='200' style='flex:1' placeholder='the idea being tested'></label>" +
      "<div id='val-confirm' class='full note' hidden><label><input type='checkbox' id='lab-confirm'> I understand a validation run uses up a look at unseen data and is logged as one.</label></div>" +
      "<div class='full'><button class='primary' id='btn-test'>Run test</button> <span class='muted small'>about a minute</span></div></div></div>" +
      "<div id='lab-result'></div><div class='card'><h2>Earlier tests</h2><div id='lab-list' class='tablewrap'></div></div>";
    el.querySelectorAll("input[name=mode]").forEach(function (r) {
      r.addEventListener("change", function () {
        var m = el.querySelector("input[name=mode]:checked").value;
        $("single-box").hidden = m !== "single"; $("matrix-box").hidden = m !== "matrix";
      });
    });
    el.querySelectorAll("input[name=period]").forEach(function (r) {
      r.addEventListener("change", function () { $("val-confirm").hidden = el.querySelector("input[name=period]:checked").value !== "validation"; });
    });
    $("btn-test").onclick = function () {
      var mode = el.querySelector("input[name=mode]:checked").value, period = el.querySelector("input[name=period]:checked").value;
      var p = { top: +$("lab-top").value, regime: $("lab-regime").value, slippage_bps: +$("lab-slip").value, period: period,
        note: $("lab-note").value, confirm_validation: period === "validation" ? $("lab-confirm").checked : undefined };
      if (mode === "single") p.signal = $("lab-signal").value;
      else { p.composite = {}; el.querySelectorAll("input[data-w]").forEach(function (i) { if (+i.value > 0) p.composite[i.dataset.w] = +i.value; }); }
      startJob("backtest", p, "Strategy test", function (j) { if (j.result_id) loadResult(j.result_id); });
    };
    renderLabList();
  }
  function renderLabList() {
    get("/api/backtests").then(function (rows) {
      var el = $("lab-list"); if (!el) return;
      if (!rows.length) { el.innerHTML = "<p class='muted'>None yet.</p>"; return; }
      el.innerHTML = "<table><thead><tr><th>When</th><th>Strategy</th><th>Period</th><th class='n'>Held</th><th>Timing</th>" +
        "<th class='n'>CAGR</th><th class='n'>Universe</th><th class='n'>Sharpe</th><th class='n'>Worst drawdown</th><th class='n'>Runs tried</th></tr></thead><tbody>" +
        rows.map(function (r) {
          return "<tr class='click' data-id='" + esc(r.id) + "'><td>" + esc(r.at.replace("T", " ")) + "</td><td>" + esc(r.label) + "</td><td>" + esc(r.period) +
            "</td><td class='n'>" + r.top + "</td><td>" + esc(r.regime) + "</td><td class='n " + cls(r.cagr - r.bench_cagr) + "'>" + pct(r.cagr) +
            "</td><td class='n'>" + pct(r.bench_cagr) + "</td><td class='n'>" + num(r.sharpe) + "</td><td class='n'>" + pct(r.max_drawdown) + "</td><td class='n'>" + r.trials + "</td></tr>";
        }).join("") + "</tbody></table>";
      el.querySelectorAll("tr.click").forEach(function (tr) { tr.addEventListener("click", function () { loadResult(tr.dataset.id); }); });
    });
  }
  function loadResult(id) {
    get("/api/backtests/" + id).then(function (r) { S.result = r; renderResult(r); });
  }
  function renderResult(r) {
    var m = r.metrics, b = r.bench, el = $("lab-result");
    var dsr = r.deflated_sharpe;
    var verdict = dsr == null ? "" : dsr >= 0.95 ? "<span class='pill ok'>unlikely to be luck</span>" : dsr >= 0.8 ? "<span class='pill warn'>could partly be luck</span>" : "<span class='pill bad'>likely luck</span>";
    var rows = [["CAGR", pct(m.cagr), pct(b.cagr)], ["Volatility", pctPlain(m.vol), pctPlain(b.vol)], ["Sharpe", num(m.sharpe), num(b.sharpe)],
      ["Sortino", num(m.sortino), num(b.sortino)], ["Worst drawdown", pct(m.max_drawdown), pct(b.max_drawdown)], ["Calmar", num(m.calmar), num(b.calmar)]];
    var ic = r.ic || {};
    el.innerHTML = "<div class='card'><div class='row spread'><h2>" + esc(r.label) + ", " + esc(r.period) + " " + esc(r.start) + " to " + esc(r.end) + "</h2>" + verdict + "</div>" +
      "<p class='muted small'>Top " + r.config.top + ", equal weight, monthly; timing " + esc(r.config.regime) + "; slippage " + (r.config.slippage * 1e4).toFixed(0) +
      " bps. Run " + r.trials + " on " + esc(r.period) + ". Deflated Sharpe probability " + (dsr == null ? "-" : num(dsr)) +
      ": the chance this Sharpe beats the best of " + r.trials + " random tries.</p>" +
      "<div class='grid2'><div class='tablewrap'><table><thead><tr><th></th><th class='n'>Strategy</th><th class='n'>Equal-weight universe</th></tr></thead><tbody>" +
      rows.map(function (x) { return "<tr><td>" + x[0] + "</td><td class='n'>" + x[1] + "</td><td class='n'>" + x[2] + "</td></tr>"; }).join("") + "</tbody></table></div>" +
      "<div class='tablewrap'><table><tbody><tr><td>IC, monthly rank correlation with next month</td><td class='n'>" + num(ic.ic_mean, 3) + " (t " + num(ic.ic_t) + ")</td></tr>" +
      "<tr><td>Months with a positive IC</td><td class='n'>" + pctPlain(ic.ic_positive, 0) + "</td></tr><tr><td>Top tenth minus bottom tenth, a month</td><td class='n'>" + pct(ic.top_minus_bottom, 2) + "</td></tr>" +
      "<tr><td>Deciles in order (1 = perfectly)</td><td class='n'>" + num(ic.monotonic) + "</td></tr><tr><td>Turnover, a year</td><td class='n'>" + num(r.costs.turnover_per_year, 1) + "x</td></tr>" +
      "<tr><td>Costs, a year</td><td class='n'>" + num(r.costs.costs_pct_per_year) + "%</td></tr></tbody></table></div></div>" +
      "<h3>Growth of Rs 1 (log scale)</h3><div id='ch-eq'></div><h3>Drawdown</h3><div id='ch-dd'></div><h3>Year by year</h3><div id='ch-yr'></div>" +
      "<div class='row' style='margin-top:12px'><input type='text' id='save-name' placeholder='name_for_this_strategy' maxlength='40'>" +
      "<button class='secondary' id='btn-save'>Save as a candidate strategy</button><span class='muted small'>Saved as a candidate, marked not validated. The scan can run it so you can watch it, but follow only a strategy that passed validation.</span></div></div>";
    lineChart($("ch-eq"), [{ name: "Strategy", points: r.nav, cls: "1" }, { name: "Equal-weight universe", points: r.bench_nav, cls: "2" }],
      { log: true, yfmt: function (v) { return num(v, v < 10 ? 1 : 0); }, tipfmt: function (v) { return inr(v, 2); }, label: "growth of one rupee" });
    lineChart($("ch-dd"), [{ name: "Drawdown", points: r.drawdown, cls: "neg", area: true }], { zero: true, height: 170, yfmt: function (v) { return pctPlain(v, 0); }, label: "drawdown" });
    var years = Object.keys(r.yearly).sort();
    barChart($("ch-yr"), years.map(function (y) { return { label: y, values: [r.yearly[y], r.bench_yearly[y]] }; }), ["Strategy", "Equal-weight universe"], { label: "returns by year" });
    $("btn-save").onclick = function () {
      var name = $("save-name").value.trim();
      startJob("save-strategy", { result_id: r.id, name: name }, "Save strategy");
    };
    el.scrollIntoView({ behavior: "smooth" });
  }

  // ------------------------------------------------------------ Paper portfolio
  function renderPaper() {
    var sc = S.scan, el = $("tab-paper");
    if (!sc || !sc.values) { el.innerHTML = "<div class='card'><p class='muted'>No paper ledger yet: run the scan with a paper capital.</p></div>"; return; }
    var start = Number(sc.capital), vals = sc.values.map(function (v) { return [v.date, Number(v.value)]; });
    var last = vals[vals.length - 1][1];
    var pos = Object.keys(sc.positions || {}).map(function (k) { var p = sc.positions[k]; p.isin = k; return p; });
    el.innerHTML = "<div class='kpis'>" + kpi("Value", inr(last), pct(last / start - 1) + " since " + esc(sc.started)) +
      kpi("Cash", inr(sc.cash), "uninvested") + kpi("Positions", pos.length, "held") + kpi("Fills", (sc.all_fills || []).length, "recorded") + "</div>" +
      "<div class='card'><h2>Paper value against the Nifty 500</h2><div id='ch-paper'></div><p class='muted small'>Both start at the paper capital. " +
      "The comparison only becomes meaningful after months of rebalances.</p></div>" +
      "<div class='card'><h2>Positions</h2><div class='tablewrap'><table><thead><tr><th>Stock</th><th class='n'>Qty</th><th class='n'>Cost</th><th class='n'>Last price</th><th class='n'>Value</th><th class='n'>Gain</th></tr></thead><tbody>" +
      (pos.length ? pos.map(function (p) {
        var v = p.qty * Number(p.last_price), g = v - Number(p.cost);
        return "<tr><td>" + esc(p.symbol) + "</td><td class='n'>" + p.qty + "</td><td class='n'>" + inr(p.cost) + "</td><td class='n'>" + inr(p.last_price, 2) +
          "</td><td class='n'>" + inr(v) + "</td><td class='n " + cls(g) + "'>" + inr(g) + "</td></tr>";
      }).join("") : "<tr><td colspan='6' class='muted'>none yet: the first orders fill at the open after the first rebalance</td></tr>") + "</tbody></table></div></div>" +
      "<div class='card'><h2>Fills</h2><div class='tablewrap'><table><thead><tr><th>Date</th><th>Side</th><th>Stock</th><th class='n'>Qty</th><th class='n'>Price</th><th class='n'>Charges</th></tr></thead><tbody>" +
      ((sc.all_fills || []).slice().reverse().map(function (f) {
        return "<tr><td>" + esc(f.date) + "</td><td>" + esc(f.side) + "</td><td>" + esc(f.symbol) + "</td><td class='n'>" + f.qty + "</td><td class='n'>" + inr(f.price, 2) + "</td><td class='n'>" + inr(f.charges, 2) + "</td></tr>";
      }).join("") || "<tr><td colspan='6' class='muted'>none yet</td></tr>") + "</tbody></table></div></div>";
    get("/api/index?name=Nifty%20500&from=" + encodeURIComponent(sc.started)).then(function (ix) {
      var series = [{ name: "Paper portfolio", points: vals, cls: "1" }];
      if (ix.length) {
        var base = ix[0][1];
        series.push({ name: "Nifty 500", points: ix.map(function (p) { return [p[0], p[1] / base * start]; }), cls: "2" });
      }
      if (vals.length < 2 && (!ix.length || ix.length < 2)) { $("ch-paper").innerHTML = "<p class='muted'>The chart starts once there are two days of values.</p>"; return; }
      lineChart($("ch-paper"), series, { yfmt: function (v) { return crore(v); }, tipfmt: function (v) { return inr(v); }, label: "paper value" });
    });
  }

  // ------------------------------------------------------------ Runs log
  function renderRuns() {
    get("/api/runs").then(function (rows) {
      var counts = {};
      rows.forEach(function (r) { counts[r.period] = (counts[r.period] || 0) + 1; });
      $("tab-runs").innerHTML = "<div class='card'><h2>Every test ever run</h2><p class='muted small'>" +
        Object.keys(counts).map(function (k) { return esc(k) + ": " + counts[k] + " run(s)"; }).join(", ") +
        ". The count is what keeps a lucky result from looking like a discovery.</p><div class='tablewrap'><table><thead><tr><th>When</th><th>Strategy</th><th>Period</th>" +
        "<th class='n'>Held</th><th>Timing</th><th class='n'>CAGR</th><th class='n'>Sharpe</th><th>Note</th></tr></thead><tbody>" +
        rows.map(function (r) {
          var c = r.config || {};
          return "<tr><td>" + esc((r.at || "").replace("T", " ")) + "</td><td>" + esc(r.signal) + "</td><td>" + esc(r.period) + "</td><td class='n'>" + (c.top || "") +
            "</td><td>" + esc(c.regime || "none") + "</td><td class='n'>" + pct(r.cagr) + "</td><td class='n'>" + num(r.sharpe) + "</td><td style='white-space:normal'>" + esc(r.note) + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    });
  }

  // ------------------------------------------------------------ start
  function refresh() {
    return Promise.all([get("/api/state"), get("/api/scan")]).then(function (x) {
      S.state = x[0]; S.scan = x[1];
      renderStatus(); renderToday();
      if (!$("tab-paper").hidden) renderPaper();
      if (!$("tab-lab").hidden) renderLabList();
    });
  }
  refresh().then(renderLab);
})();
