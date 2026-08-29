"""Generate the static frontend (PLAN phase 5).

Reads the database and writes a SELF-CONTAINED HTML file: the scholarship data is
embedded as JSON and all filtering happens in the browser. That keeps the whole
thing free and safe — there is no server to host and no database credential in the
page, which rules out the obvious "just let the frontend query Neon" approach.

  python -m src.export_site                    # -> site/index.html
  python -m src.export_site --out docs/index.html
  python -m src.export_site --fragment         # body-only, for embedding

Publish `site/index.html` on GitHub Pages, or just open it locally — it needs
nothing but a browser.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import config, db
from .vocab import DEGREE_LEVELS, FIELDS, FUNDING_TYPES

ROWS_SQL = """
    SELECT title, provider, country, region, degree_levels, fields, field_raw,
           funding_type, funding_details, ielts_required, ielts_min, other_language,
           deadline, deadline_raw, is_open, apply_url, summary, eligibility,
           source_id, last_seen
      FROM scholarships
     ORDER BY (deadline IS NULL), deadline ASC, title ASC
"""


def fetch_rows() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(ROWS_SQL).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["deadline"] = d["deadline"].isoformat() if d["deadline"] else None
        d["last_seen"] = d["last_seen"].isoformat() if d.get("last_seen") else None
        if d.get("ielts_min") is not None:
            d["ielts_min"] = float(d["ielts_min"])
        out.append(d)
    return out


def build_payload(rows: list[dict]) -> dict:
    """Data plus the facet values actually present, so no filter offers a dead end."""
    present = lambda key: sorted({v for r in rows for v in (r.get(key) or [])})
    countries = sorted({r["country"] for r in rows if r.get("country")})
    return {
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "today": date.today().isoformat(),
        "rows": rows,
        "facets": {
            # Intersect with the controlled vocabulary so ordering stays meaningful
            # (bachelors -> masters -> phd), not alphabetical.
            "levels": [x for x in DEGREE_LEVELS if x in set(present("degree_levels"))],
            "fields": [x for x in FIELDS if x in set(present("fields"))],
            "funding": [x for x in FUNDING_TYPES
                        if x in {r.get("funding_type") for r in rows}],
            "countries": countries,
        },
    }


# --------------------------------------------------------------------------- markup

_STYLE_AND_BODY = r"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">

<style>
  /* Light palette is the complete set; dark redefines only the tokens.
     Neutrals carry a slight green bias so they read as chosen next to the teal. */
  :root {
    --ground:#F1F4F3; --surface:#FFFFFF; --surface-2:#F7F9F8;
    --ink:#131E1D; --muted:#5B6A69; --line:#DCE3E1;
    --accent:#0F5F5C; --accent-soft:#DCEBE9;
    --urgent:#A8431D; --urgent-soft:#F7E4DB;
    --good:#2E6B3C; --good-soft:#E0EDE2;
    --shadow:0 1px 2px rgba(19,30,29,.06), 0 8px 24px -16px rgba(19,30,29,.25);
    --radius:10px;
  }
  :root:not([data-theme="light"]) {
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground:#0D1413; --surface:#151F1E; --surface-2:#1B2625;
      --ink:#E7EEEC; --muted:#93A5A2; --line:#273433;
      --accent:#5CC3B8; --accent-soft:#12302E;
      --urgent:#F0916A; --urgent-soft:#33201A;
      --good:#7CC489; --good-soft:#17301C;
      --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -18px rgba(0,0,0,.8);
      color-scheme: dark;
    }
  }
  :root[data-theme="dark"] {
    --ground:#0D1413; --surface:#151F1E; --surface-2:#1B2625;
    --ink:#E7EEEC; --muted:#93A5A2; --line:#273433;
    --accent:#5CC3B8; --accent-soft:#12302E;
    --urgent:#F0916A; --urgent-soft:#33201A;
    --good:#7CC489; --good-soft:#17301C;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -18px rgba(0,0,0,.8);
    color-scheme: dark;
  }

  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--ground); color:var(--ink);
    font-family:"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size:15px; line-height:1.5;
    -webkit-font-smoothing:antialiased;
  }
  h1,h2,h3 { font-family:"Bricolage Grotesque", "IBM Plex Sans", sans-serif;
             text-wrap:balance; margin:0; font-weight:700; }
  a { color:var(--accent); }
  .mono { font-family:"IBM Plex Mono", ui-monospace, SFMono-Regular, monospace;
          font-variant-numeric:tabular-nums; }

  .wrap { max-width:1180px; margin:0 auto; padding:28px 20px 64px; }

  header.top { display:flex; align-items:flex-end; justify-content:space-between;
               gap:16px; flex-wrap:wrap; margin-bottom:20px; }
  header.top h1 { font-size:clamp(24px,3.4vw,34px); letter-spacing:-.02em; }
  .sub { color:var(--muted); font-size:13.5px; margin-top:4px; }

  .themebtn { font:inherit; font-size:13px; color:var(--muted); cursor:pointer;
              background:var(--surface); border:1px solid var(--line);
              border-radius:99px; padding:7px 14px; }
  .themebtn:hover { color:var(--ink); border-color:var(--accent); }

  /* Summary strip: the three numbers worth knowing before filtering. */
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:10px; margin-bottom:22px; }
  .stat { background:var(--surface); border:1px solid var(--line);
          border-radius:var(--radius); padding:13px 15px; }
  .stat .n { font-family:"Bricolage Grotesque",sans-serif; font-size:26px;
             font-weight:700; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
  .stat .k { font-size:11.5px; text-transform:uppercase; letter-spacing:.09em;
             color:var(--muted); margin-top:2px; }
  .stat.urgent .n { color:var(--urgent); }

  .layout { display:grid; grid-template-columns:250px 1fr; gap:24px; align-items:start; }
  @media (max-width:860px) { .layout { grid-template-columns:1fr; } }

  .panel { background:var(--surface); border:1px solid var(--line);
           border-radius:var(--radius); padding:16px; box-shadow:var(--shadow); }
  aside.panel { position:sticky; top:18px; }
  @media (max-width:860px) { aside.panel { position:static; } }

  .fgroup + .fgroup { margin-top:16px; padding-top:16px; border-top:1px solid var(--line); }
  .flabel { font-size:11.5px; text-transform:uppercase; letter-spacing:.09em;
            color:var(--muted); margin-bottom:8px; font-weight:600; }
  select, input[type=search] {
    width:100%; font:inherit; font-size:14px; color:var(--ink);
    background:var(--surface-2); border:1px solid var(--line);
    border-radius:8px; padding:8px 10px;
  }
  select:focus-visible, input:focus-visible, button:focus-visible, a:focus-visible {
    outline:2px solid var(--accent); outline-offset:2px;
  }
  .checks { display:flex; flex-direction:column; gap:6px; }
  .check { display:flex; align-items:center; gap:8px; font-size:13.5px;
           cursor:pointer; color:var(--ink); }
  .check input { accent-color:var(--accent); width:15px; height:15px; margin:0; }
  .check .cnt { margin-left:auto; font-size:11.5px; color:var(--muted);
                font-variant-numeric:tabular-nums; }

  .clear { margin-top:16px; width:100%; font:inherit; font-size:13px; cursor:pointer;
           background:transparent; color:var(--muted);
           border:1px solid var(--line); border-radius:8px; padding:8px; }
  .clear:hover { color:var(--urgent); border-color:var(--urgent); }

  .resulthead { display:flex; align-items:baseline; justify-content:space-between;
                gap:12px; margin-bottom:12px; flex-wrap:wrap; }
  .count { font-size:13.5px; color:var(--muted); }
  .count b { color:var(--ink); font-variant-numeric:tabular-nums; }

  .list { display:flex; flex-direction:column; gap:10px; }

  /* Each row carries a left stripe whose colour encodes deadline urgency —
     structure that says something true, rather than decoration. */
  .card { background:var(--surface); border:1px solid var(--line);
          border-left:3px solid var(--line);
          border-radius:var(--radius); padding:14px 16px; box-shadow:var(--shadow); }
  .card.soon { border-left-color:var(--urgent); }
  .card.open { border-left-color:var(--accent); }
  .card.closed { opacity:.62; }
  .card h3 { font-size:16.5px; letter-spacing:-.01em; line-height:1.3; }
  .prov { font-size:13px; color:var(--muted); margin-top:3px; }
  .sum { font-size:13.5px; color:var(--muted); margin-top:8px; }

  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .chip { font-size:11.5px; letter-spacing:.02em; padding:3px 9px; border-radius:99px;
          background:var(--surface-2); color:var(--muted); border:1px solid var(--line); }
  .chip.lvl { background:var(--accent-soft); color:var(--accent);
              border-color:transparent; font-weight:600; }
  .chip.fund-fully_funded { background:var(--good-soft); color:var(--good);
                            border-color:transparent; font-weight:600; }
  .chip.dl { background:transparent; }
  .chip.dl.soon { background:var(--urgent-soft); color:var(--urgent);
                  border-color:transparent; font-weight:600; }
  .chip.dl.closed { text-decoration:line-through; }

  .cardfoot { display:flex; align-items:center; justify-content:space-between;
              gap:12px; margin-top:11px; flex-wrap:wrap; }
  .apply { font-size:13.5px; font-weight:600; text-decoration:none; }
  .apply:hover { text-decoration:underline; }
  .src { font-size:11.5px; color:var(--muted); }

  .empty { text-align:center; padding:48px 20px; color:var(--muted); }
  .empty h3 { font-size:17px; color:var(--ink); margin-bottom:6px; }

  footer { margin-top:34px; padding-top:16px; border-top:1px solid var(--line);
           font-size:12.5px; color:var(--muted); display:flex;
           justify-content:space-between; gap:12px; flex-wrap:wrap; }

  @media (prefers-reduced-motion:reduce) { * { animation:none!important; transition:none!important; } }
</style>

<div class="wrap">
  <header class="top">
    <div>
      <h1>Scholarship Finder</h1>
      <div class="sub">Bachelors &amp; Masters funding across Europe, the UK and beyond ·
        <span class="mono" id="gen"></span></div>
    </div>
    <button class="themebtn" id="theme" type="button">Theme</button>
  </header>

  <section class="stats" id="stats"></section>

  <div class="layout">
    <aside class="panel">
      <div class="fgroup">
        <div class="flabel">Search</div>
        <input type="search" id="q" placeholder="Title, provider, subject…" autocomplete="off">
      </div>
      <div class="fgroup">
        <div class="flabel">Status</div>
        <div class="checks" id="f-status"></div>
      </div>
      <div class="fgroup">
        <div class="flabel">Degree level</div>
        <div class="checks" id="f-levels"></div>
      </div>
      <div class="fgroup">
        <div class="flabel">Funding</div>
        <div class="checks" id="f-funding"></div>
      </div>
      <div class="fgroup">
        <div class="flabel">Field</div>
        <select id="f-field"><option value="">All fields</option></select>
      </div>
      <div class="fgroup">
        <div class="flabel">Destination</div>
        <select id="f-country"><option value="">Anywhere</option></select>
      </div>
      <div class="fgroup">
        <div class="flabel">Max IELTS</div>
        <select id="f-ielts">
          <option value="">Any requirement</option>
          <option value="5.5">5.5 or lower</option>
          <option value="6">6.0 or lower</option>
          <option value="6.5">6.5 or lower</option>
          <option value="7">7.0 or lower</option>
          <option value="none">Not stated</option>
        </select>
      </div>
      <button class="clear" id="clear" type="button">Clear all filters</button>
    </aside>

    <main>
      <div class="resulthead">
        <div class="count" id="count"></div>
        <div>
          <select id="sort" style="width:auto">
            <option value="deadline">Soonest deadline</option>
            <option value="title">Title A–Z</option>
            <option value="country">Destination</option>
          </select>
        </div>
      </div>
      <div class="list" id="list"></div>
    </main>
  </div>

  <footer>
    <span>Collected automatically from official sources. Always confirm details on the provider's own page.</span>
    <span class="mono" id="foot"></span>
  </footer>
</div>

<script>
(function () {
  "use strict";
  var DATA = window.__SCHOLARSHIPS__;
  var rows = DATA.rows, today = DATA.today;

  var LABEL = {
    any_field:"Any field", computer_science:"Computer science", engineering:"Engineering",
    natural_sciences:"Natural sciences", medicine_health:"Medicine & health",
    business_economics:"Business & economics", social_sciences:"Social sciences",
    law:"Law", arts_humanities:"Arts & humanities", education:"Education",
    agriculture:"Agriculture", bachelors:"Bachelors", masters:"Masters", phd:"PhD",
    fully_funded:"Fully funded", partial:"Partial", unknown:"Funding unclear"
  };
  function label(v) { return LABEL[v] || v; }

  function daysLeft(d) {
    if (!d) return null;
    return Math.round((new Date(d + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
  }

  // ---- filter state
  var state = { q:"", status:["open"], levels:[], funding:[], field:"", country:"", ielts:"", sort:"deadline" };

  function matches(r) {
    var dl = daysLeft(r.deadline);
    if (state.status.length) {
      var isOpen = r.is_open !== false;
      var soon = dl !== null && dl >= 0 && dl <= 30;
      var ok = state.status.some(function (s) {
        if (s === "open") return isOpen;
        if (s === "soon") return soon;
        if (s === "closed") return !isOpen;
        return true;
      });
      if (!ok) return false;
    }
    if (state.levels.length && !state.levels.some(function (l) {
      return (r.degree_levels || []).indexOf(l) > -1; })) return false;
    if (state.funding.length && state.funding.indexOf(r.funding_type) < 0) return false;
    if (state.field && (r.fields || []).indexOf(state.field) < 0) return false;
    if (state.country && r.country !== state.country) return false;
    if (state.ielts === "none") { if (r.ielts_min !== null) return false; }
    else if (state.ielts) {
      // An unstated requirement is not a barrier, so keep those rows.
      if (r.ielts_min !== null && r.ielts_min > parseFloat(state.ielts)) return false;
    }
    if (state.q) {
      var hay = [r.title, r.provider, r.country, r.field_raw, r.summary,
                 (r.fields || []).join(" ")].join(" ").toLowerCase();
      if (hay.indexOf(state.q.toLowerCase()) < 0) return false;
    }
    return true;
  }

  function sortRows(list) {
    var s = state.sort;
    return list.slice().sort(function (a, b) {
      if (s === "title") return a.title.localeCompare(b.title);
      if (s === "country") return (a.country || "~").localeCompare(b.country || "~")
                               || a.title.localeCompare(b.title);
      var da = a.deadline, dbb = b.deadline;
      if (!da && !dbb) return a.title.localeCompare(b.title);
      if (!da) return 1;
      if (!dbb) return -1;
      return da < dbb ? -1 : da > dbb ? 1 : 0;
    });
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function deadlineChip(r) {
    var dl = daysLeft(r.deadline);
    if (r.deadline === null) {
      var raw = r.deadline_raw ? "No fixed deadline" : "No deadline stated";
      return '<span class="chip dl mono">' + esc(raw) + "</span>";
    }
    var cls = "chip dl mono" + (dl !== null && dl >= 0 && dl <= 30 ? " soon" : "")
            + (dl !== null && dl < 0 ? " closed" : "");
    var when = dl < 0 ? "Closed " + r.deadline
             : dl === 0 ? "Closes today"
             : dl === 1 ? "Closes tomorrow"
             : "Closes in " + dl + " days · " + r.deadline;
    return '<span class="' + cls + '">' + esc(when) + "</span>";
  }

  function card(r) {
    var dl = daysLeft(r.deadline);
    var cls = "card " + (r.is_open === false ? "closed"
                       : (dl !== null && dl >= 0 && dl <= 30) ? "soon" : "open");
    var chips = "";
    (r.degree_levels || []).forEach(function (l) {
      chips += '<span class="chip lvl">' + esc(label(l)) + "</span>"; });
    chips += '<span class="chip fund-' + esc(r.funding_type) + '">'
           + esc(label(r.funding_type)) + "</span>";
    if (r.country) chips += '<span class="chip">' + esc(r.country) + "</span>";
    if (r.ielts_min !== null) chips += '<span class="chip mono">IELTS ' + r.ielts_min + "</span>";
    chips += deadlineChip(r);

    var fieldNames = (r.fields || []).slice(0, 4).map(label).join(" · ");
    return '<article class="' + cls + '">'
      + "<h3>" + esc(r.title) + "</h3>"
      + '<div class="prov">' + esc(r.provider || "—")
        + (fieldNames ? " · " + esc(fieldNames) : "") + "</div>"
      + (r.summary ? '<div class="sum">' + esc(r.summary) + "</div>" : "")
      + '<div class="chips">' + chips + "</div>"
      + '<div class="cardfoot">'
        + '<a class="apply" href="' + esc(r.apply_url) + '" target="_blank" rel="noopener">Open the official page →</a>'
        + '<span class="src mono">' + esc(r.source_id) + "</span>"
      + "</div></article>";
  }

  function render() {
    var shown = sortRows(rows.filter(matches));
    document.getElementById("count").innerHTML =
      "<b>" + shown.length + "</b> of " + rows.length + " scholarships";
    var list = document.getElementById("list");
    list.innerHTML = shown.length
      ? shown.map(card).join("")
      : '<div class="empty panel"><h3>Nothing matches these filters</h3>'
        + "<div>Try clearing the IELTS cap or the field filter — most entries do not state an IELTS score.</div></div>";
  }

  // ---- summary strip
  function stats() {
    var open = 0, soon = 0, funded = 0;
    rows.forEach(function (r) {
      var dl = daysLeft(r.deadline);
      if (r.is_open !== false) open++;
      if (dl !== null && dl >= 0 && dl <= 30) soon++;
      if (r.funding_type === "fully_funded" && r.is_open !== false) funded++;
    });
    var cells = [
      { n: rows.length, k: "in the database" },
      { n: open, k: "currently open" },
      { n: funded, k: "open & fully funded" },
      { n: soon, k: "closing within 30 days", urgent: true }
    ];
    document.getElementById("stats").innerHTML = cells.map(function (c) {
      return '<div class="stat' + (c.urgent && c.n ? " urgent" : "") + '">'
        + '<div class="n">' + c.n + "</div><div class=\"k\">" + c.k + "</div></div>";
    }).join("");
  }

  // ---- build the controls from the data that is actually present
  function checkbox(host, name, value, text, count, checked) {
    var id = name + "-" + value;
    var el = document.createElement("label");
    el.className = "check";
    el.setAttribute("for", id);
    el.innerHTML = '<input type="checkbox" id="' + id + '" value="' + esc(value) + '"'
      + (checked ? " checked" : "") + ">" + "<span>" + esc(text) + "</span>"
      + (count === null ? "" : '<span class="cnt">' + count + "</span>");
    host.appendChild(el);
  }

  function countBy(pred) { return rows.filter(pred).length; }

  function buildControls() {
    var f = DATA.facets;

    var st = document.getElementById("f-status");
    checkbox(st, "st", "open", "Open", countBy(function (r) { return r.is_open !== false; }), true);
    checkbox(st, "st", "soon", "Closing in 30 days",
      countBy(function (r) { var d = daysLeft(r.deadline); return d !== null && d >= 0 && d <= 30; }), false);
    checkbox(st, "st", "closed", "Closed",
      countBy(function (r) { return r.is_open === false; }), false);

    var lv = document.getElementById("f-levels");
    f.levels.forEach(function (l) {
      checkbox(lv, "lv", l, label(l),
        countBy(function (r) { return (r.degree_levels || []).indexOf(l) > -1; }), false);
    });

    var fu = document.getElementById("f-funding");
    f.funding.forEach(function (v) {
      checkbox(fu, "fu", v, label(v),
        countBy(function (r) { return r.funding_type === v; }), false);
    });

    var fs = document.getElementById("f-field");
    f.fields.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v;
      o.textContent = label(v) + " (" + countBy(function (r) {
        return (r.fields || []).indexOf(v) > -1; }) + ")";
      fs.appendChild(o);
    });

    var fc = document.getElementById("f-country");
    f.countries.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v;
      o.textContent = v + " (" + countBy(function (r) { return r.country === v; }) + ")";
      fc.appendChild(o);
    });
  }

  function readChecks(hostId) {
    return Array.prototype.slice
      .call(document.querySelectorAll("#" + hostId + " input:checked"))
      .map(function (i) { return i.value; });
  }

  function wire() {
    document.getElementById("q").addEventListener("input", function (e) {
      state.q = e.target.value; render();
    });
    ["f-status", "f-levels", "f-funding"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function () {
        state.status = readChecks("f-status");
        state.levels = readChecks("f-levels");
        state.funding = readChecks("f-funding");
        render();
      });
    });
    document.getElementById("f-field").addEventListener("change", function (e) {
      state.field = e.target.value; render(); });
    document.getElementById("f-country").addEventListener("change", function (e) {
      state.country = e.target.value; render(); });
    document.getElementById("f-ielts").addEventListener("change", function (e) {
      state.ielts = e.target.value; render(); });
    document.getElementById("sort").addEventListener("change", function (e) {
      state.sort = e.target.value; render(); });

    document.getElementById("clear").addEventListener("click", function () {
      document.getElementById("q").value = "";
      Array.prototype.slice.call(document.querySelectorAll(".checks input"))
        .forEach(function (i) { i.checked = (i.value === "open"); });
      ["f-field", "f-country", "f-ielts"].forEach(function (id) {
        document.getElementById(id).value = ""; });
      state = { q:"", status:["open"], levels:[], funding:[], field:"", country:"",
                ielts:"", sort:state.sort };
      render();
    });

    document.getElementById("theme").addEventListener("click", function () {
      var root = document.documentElement;
      var dark = getComputedStyle(root).getPropertyValue("--ground").trim() === "#0D1413";
      root.setAttribute("data-theme", dark ? "light" : "dark");
      try { localStorage.setItem("sf-theme", dark ? "light" : "dark"); } catch (e) {}
    });
    try {
      var saved = localStorage.getItem("sf-theme");
      if (saved) document.documentElement.setAttribute("data-theme", saved);
    } catch (e) {}
  }

  document.getElementById("gen").textContent = "updated " + DATA.generated;
  document.getElementById("foot").textContent = rows.length + " records · " + DATA.generated;
  stats();
  buildControls();
  wire();
  render();
})();
</script>
"""


def render_html(payload: dict, fragment: bool = False) -> str:
    # </script> inside JSON would end the block early; < keeps it inert.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    body = (
        '<script>window.__SCHOLARSHIPS__ = ' + data + ";</script>\n" + _STYLE_AND_BODY
    )
    title = "Scholarship Finder"
    if fragment:
        return f"<title>{title}</title>\n{body}"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def main() -> int:
    config.enable_utf8_output()
    ap = argparse.ArgumentParser(description="Generate the static scholarship site.")
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--fragment", action="store_true",
                    help="emit body-only markup (no <html>/<head> wrapper)")
    args = ap.parse_args()

    rows = fetch_rows()
    if not rows:
        print("No scholarships in the database yet — run the pipeline first.")
        return 1

    html = render_html(build_payload(rows), fragment=args.fragment)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} — {len(rows)} scholarships, {len(html):,} bytes")
    print("Open it in a browser, or publish the folder with GitHub Pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
