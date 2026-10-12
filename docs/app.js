/* Ostwal Fund House CRM: a small single-page app on top of Supabase. */
(function () {
  "use strict";
  const cfg = window.CRM_CONFIG;
  const sb = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);
  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $("#view");
  let me = null;

  /* ---------- formatting ---------- */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  const inr2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const u3 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const rs = (v) => {
    if (v == null) return "–";
    const r = Math.round(v);
    return (r < 0 ? "−₹" : "₹") + inr.format(Math.abs(r));
  };
  const short = (v) => {
    if (v == null) return "–";
    const a = Math.abs(v), s = v < 0 ? "−₹" : "₹";
    if (a >= 1e7) return s + (a / 1e7).toFixed(2) + " Cr";
    if (a >= 1e5) return s + (a / 1e5).toFixed(2) + " L";
    return rs(v);
  };
  const pct = (g, c) => {
    if (!(c > 0)) return "–";
    const p = (g / c) * 100;
    return (Math.abs(p) < 0.05 ? "0.0" : p.toFixed(1)) + "%";
  };
  const cls = (v) => (v > 0 ? "gain" : v < 0 ? "loss" : "");
  const dmy = (d) => (d ? d.split("-").reverse().join("/") : "–");
  const arnColor = (a) => cfg.ARN_COLORS[a] || "#8a9493";
  const arnTag = (a) => (a ? `<span class="arn" style="background:${arnColor(a)}">${esc(a)}</span>` : "");
  const regName = (r) => (r === "KFIN" ? "KFintech" : r === "CAMS" ? "CAMS" : r);

  /* ---------- data helpers ---------- */
  async function all(query, page = 1000) {
    // Fetch every row of a query in pages.
    let out = [], from = 0;
    for (;;) {
      const { data, error } = await query().range(from, from + page - 1);
      if (error) throw error;
      out = out.concat(data);
      if (data.length < page) return out;
      from += page;
    }
  }
  function fail(err) {
    view.innerHTML = `<h1 class="page">Something went wrong</h1><p class="sub">${esc(err.message || err)}</p>`;
    console.error(err);
  }
  function table(cols, rows, opts = {}) {
    if (!rows.length) return `<div class="panel"><div class="empty">${esc(opts.empty || "Nothing to show yet.")}</div></div>`;
    const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.h)}</th>`).join("");
    const body = rows.map((r) => {
      const attr = opts.link ? ` class="click" data-href="${esc(opts.link(r))}" tabindex="0"` : "";
      return `<tr${attr}>` + cols.map((c) => `<td class="${c.num ? "num" : ""} ${c.cls ? c.cls(r) : ""}">${c.f(r)}</td>`).join("") + "</tr>";
    }).join("");
    return `<div class="panel"><div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${opts.foot || ""}</div>`;
  }
  view.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr && !e.target.closest("a,button")) location.hash = tr.dataset.href;
  });
  view.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr && e.key === "Enter") location.hash = tr.dataset.href;
  });

  /* ---------- pages ---------- */
  async function home() {
    view.innerHTML = `<h1 class="page">Overview</h1><p class="sub">Loading…</p>`;
    const [hv, clientCount, recent, imports] = await Promise.all([
      all(() => sb.from("holding_values").select("arn,registrar,amc_code,scheme_name,units,cost_value,market_value").gt("units", 0.001)),
      sb.from("clients").select("pan", { count: "exact", head: true }).gt("active_holdings", 0),
      sb.from("transactions").select("trxn_date,inv_name,scheme_name,trxn_type,trxn_desc,amount,sign,arn,pan,folio_no,registrar")
        .neq("sign", 0).order("trxn_date", { ascending: false }).limit(15),
      sb.from("import_log").select("file_name,status,finished_at,started_at").order("id", { ascending: false }).limit(1),
    ]);
    const mv = hv.reduce((s, r) => s + (r.market_value || 0), 0);
    const cv = hv.reduce((s, r) => s + (r.cost_value || 0), 0);
    const byArn = groupSum(hv, (r) => r.arn || "Other");
    const byAmc = groupSum(hv, (r) => amcLabel(r));
    const last = imports.data && imports.data[0];
    view.innerHTML = `
      <h1 class="page">Overview</h1>
      <p class="sub">${last ? "Last import " + esc(new Date(last.finished_at || last.started_at).toLocaleString("en-IN")) : "No files imported yet."}</p>
      <div class="money">
        <div><div class="v">${short(mv)}</div><div class="l">Market value</div></div>
        <div><div class="v">${short(cv)}</div><div class="l">Cost value</div></div>
        <div><div class="v ${cls(mv - cv)}">${short(mv - cv)}</div><div class="l">Gain (${pct(mv - cv, cv)})</div></div>
        <div><div class="v">${inr.format(clientCount.count || 0)}</div><div class="l">Clients with holdings</div></div>
      </div>
      <div class="grid2">
        <section><h2>By ARN</h2><div class="panel">${bars(byArn, (k) => arnColor(k))}</div></section>
        <section><h2>By fund house</h2><div class="panel">${bars(byAmc.slice(0, 10), () => "var(--ink)")}</div></section>
      </div>
      <h2>Latest transactions</h2>
      ${table([
        { h: "Date", f: (r) => dmy(r.trxn_date) },
        { h: "Client", f: (r) => esc(r.inv_name) },
        { h: "Scheme", f: (r) => esc(r.scheme_name || "") },
        { h: "Type", f: (r) => esc(r.trxn_desc || r.trxn_type) },
        { h: "Amount", num: 1, f: (r) => rs(r.amount), cls: (r) => (r.sign < 0 ? "loss" : "") },
        { h: "ARN", f: (r) => arnTag(r.arn) },
      ], recent.data || [], { link: (r) => (r.pan ? "#/client/" + r.pan : "#/folio/" + r.registrar + "/" + r.folio_no) })}`;
  }

  function groupSum(rows, keyFn) {
    const m = new Map();
    rows.forEach((r) => m.set(keyFn(r), (m.get(keyFn(r)) || 0) + (r.market_value || 0)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }
  function bars(entries, colorFn) {
    if (!entries.length) return `<div class="empty">No holdings yet.</div>`;
    const max = entries[0][1] || 1;
    return `<div class="bars">` + entries.map(([k, v]) => `
      <div class="bar"><span>${esc(k)}</span>
        <span class="track"><span class="fill" style="display:block;width:${Math.max(1, (v / max) * 100)}%;background:${colorFn(k)}"></span></span>
        <span class="num">${short(v)}</span></div>`).join("") + `</div>`;
  }
  function amcLabel(r) {
    // Scheme names start with the fund house name; use the first two words.
    const n = (r.scheme_name || r.amc_code || "Other").split(/\s+/).slice(0, 2).join(" ");
    return n;
  }

  async function clients(params) {
    const q = (params.get("q") || "").trim();
    const page = Number(params.get("p") || 0);
    const size = 50;
    view.innerHTML = `
      <h1 class="page">Clients</h1>
      <p class="sub">One row per PAN, across CAMS and KFintech and all three ARNs.</p>
      <form class="toolbar" id="search">
        <input type="search" name="q" value="${esc(q)}" placeholder="Name, PAN, mobile or folio number" aria-label="Search clients">
        <button>Search</button>
      </form><div id="list"><p class="muted">Loading…</p></div>`;
    $("#search").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = new FormData(e.target).get("q");
      location.hash = "#/clients" + (v ? "?q=" + encodeURIComponent(v) : "");
    });
    let query = sb.from("clients").select("pan,inv_name,city,mobile,arns,active_holdings,market_value,cost_value", { count: "exact" });
    if (q) {
      const like = `%${q.replace(/[%,()"\\]/g, " ").trim()}%`;
      let pans = [];
      if (/^\d{5,}$/.test(q)) {
        const f = await sb.from("folios").select("pan").ilike("folio_no", like).limit(50);
        pans = (f.data || []).map((r) => r.pan).filter(Boolean);
      }
      const ors = [`inv_name.ilike."${like}"`, `pan.ilike."${like}"`, `mobile.ilike."${like}"`];
      if (pans.length) ors.push(`pan.in.(${[...new Set(pans)].join(",")})`);
      query = query.or(ors.join(","));
    }
    const { data, count, error } = await query.order("market_value", { ascending: false })
      .range(page * size, page * size + size - 1);
    if (error) return fail(error);
    const pages = Math.ceil((count || 0) / size);
    const foot = pages > 1 ? `<div class="pager">
      ${page > 0 ? `<a href="#/clients?${new URLSearchParams({ q, p: page - 1 })}">Previous</a>` : ""}
      <span class="muted">Page ${page + 1} of ${pages}</span>
      ${page + 1 < pages ? `<a href="#/clients?${new URLSearchParams({ q, p: page + 1 })}">Next</a>` : ""}</div>` : "";
    $("#list").innerHTML = table([
      { h: "Name", f: (r) => `<a href="#/client/${esc(r.pan)}">${esc(r.inv_name)}</a>` },
      { h: "PAN", f: (r) => esc(r.pan) },
      { h: "City", f: (r) => esc(r.city) },
      { h: "Mobile", f: (r) => esc(r.mobile) },
      { h: "ARN", f: (r) => (r.arns || []).filter(Boolean).map(arnTag).join(" ") },
      { h: "Holdings", num: 1, f: (r) => r.active_holdings },
      { h: "Value", num: 1, f: (r) => rs(r.market_value) },
      { h: "Gain", num: 1, f: (r) => rs(r.market_value - r.cost_value), cls: (r) => cls(r.market_value - r.cost_value) },
    ], data, { link: (r) => "#/client/" + r.pan, foot, empty: q ? "No client matches that search." : "No clients yet. Upload the investor master files first." });
  }

  async function client(pan) {
    view.innerHTML = `<p class="muted">Loading…</p>`;
    const [folios, hv, notes, fam] = await Promise.all([
      sb.from("folios").select("*").eq("pan", pan),
      sb.from("holding_values").select("*").eq("pan", pan),
      sb.from("client_notes").select("*").eq("pan", pan).order("created_at", { ascending: false }),
      sb.from("family_members").select("family_id, families(name)").eq("pan", pan).maybeSingle(),
    ]);
    if (folios.error) return fail(folios.error);
    const fs = folios.data || [];
    if (!fs.length) {
      view.innerHTML = `<h1 class="page">${esc(pan)}</h1><p class="sub">No folios with this PAN.</p>`;
      return;
    }
    const latest = [...fs].sort((a, b) => String(b.report_date).localeCompare(String(a.report_date)))[0];
    const folioNos = [...new Set(fs.map((f) => f.folio_no))];
    const [txns, hist] = await Promise.all([
      all(() => sb.from("transactions").select("registrar,folio_no,product_code,scheme_name,trxn_date,trxn_type,trxn_desc,units,amount,price,sign,arn,is_reversal")
        .in("folio_no", folioNos).order("trxn_date", { ascending: false })),
      all(() => sb.from("arn_history").select("*").in("folio_no", folioNos)),
    ]);
    const holdings = (hv.data || []).sort((a, b) => (b.market_value || 0) - (a.market_value || 0));
    const active = holdings.filter((h) => h.units > 0.001);
    const mv = active.reduce((s, h) => s + (h.market_value || 0), 0);
    const cv = active.reduce((s, h) => s + (h.cost_value || 0), 0);
    const nominees = uniq(fs.flatMap((f) => [f.nominee1_name, f.nominee2_name, f.nominee3_name]).filter(Boolean));
    const banks = uniq(fs.map((f) => [f.bank_name, f.bank_ac_no ? "A/c " + f.bank_ac_no : "", f.bank_ifsc].filter(Boolean).join(", ")).filter(Boolean));
    const famName = fam.data && fam.data.families ? fam.data.families.name : null;

    view.innerHTML = `
      <h1 class="page">${esc(latest.inv_name)}</h1>
      <p class="sub">${esc(pan)} · ${esc(latest.city || "")} ${famName ? `· Family: <a href="#/family/${fam.data.family_id}">${esc(famName)}</a>` : ""}</p>
      <div class="money">
        <div><div class="v">${rs(mv)}</div><div class="l">Market value</div></div>
        <div><div class="v">${rs(cv)}</div><div class="l">Cost value</div></div>
        <div><div class="v ${cls(mv - cv)}">${rs(mv - cv)}</div><div class="l">Gain (${pct(mv - cv, cv)})</div></div>
        <div><div class="v">${active.length}</div><div class="l">Active holdings</div></div>
      </div>
      <h2>Details</h2>
      <dl class="facts">
        ${fact("Mobile", latest.mobile)}${fact("Email", latest.email)}${fact("Date of birth", dmy(latest.dob))}
        ${fact("Address", [latest.address1, latest.address2, latest.address3, latest.city, latest.pincode].filter(Boolean).join(", "))}
        ${fact("Occupation", latest.occupation)}${fact("Tax status", latest.tax_status)}
        ${fact("Holding", latest.holding_mode)}${fact("Joint holders", uniq(fs.flatMap((f) => [f.joint1_name, f.joint2_name]).filter(Boolean)).join(", ") || "None")}
        ${fact("Nominees", nominees.join(", ") || "None recorded")}
        ${fact("Bank accounts", banks.join("<br>"), true)}
      </dl>
      <h2>Holdings</h2>
      ${table([
        { h: "Scheme", f: (h) => esc(h.scheme_name || h.product_code) },
        { h: "Folio", f: (h) => esc(h.folio_no) + ` <span class="muted">${regName(h.registrar)}</span>` },
        { h: "ARN", f: (h) => arnTag(h.arn) },
        { h: "Units", num: 1, f: (h) => u3.format(h.units) },
        { h: "NAV", num: 1, f: (h) => (h.nav ? inr2.format(h.nav) + ` <span class="muted">${dmy(h.nav_date)}</span>` : "–") },
        { h: "Cost", num: 1, f: (h) => rs(h.cost_value) },
        { h: "Value", num: 1, f: (h) => rs(h.market_value) },
        { h: "Gain", num: 1, f: (h) => rs((h.market_value || 0) - h.cost_value), cls: (h) => cls((h.market_value || 0) - h.cost_value) },
      ], active, { empty: "No units held at the moment." })}
      ${holdings.length > active.length ? `<p class="muted">${holdings.length - active.length} fully redeemed folio-schemes are listed under ARN history below.</p>` : ""}
      <h2>ARN history by year</h2>
      ${arnHistoryTable(hist, fs)}
      <h2>Transactions</h2>
      ${table([
        { h: "Date", f: (t) => dmy(t.trxn_date) },
        { h: "Scheme", f: (t) => esc(t.scheme_name || t.product_code) },
        { h: "Folio", f: (t) => esc(t.folio_no) },
        { h: "Type", f: (t) => esc(t.trxn_desc || t.trxn_type) + (t.is_reversal ? ' <span class="tag">reversal</span>' : "") },
        { h: "Units", num: 1, f: (t) => (t.units == null ? "" : u3.format(t.units)), cls: (t) => (t.sign < 0 ? "loss" : "") },
        { h: "Amount", num: 1, f: (t) => (t.amount == null ? "" : rs(t.amount)) },
        { h: "ARN", f: (t) => arnTag(t.arn) },
      ], txns.filter((t) => t.sign !== 0 || t.units), { empty: "No transactions loaded for these folios." })}
      <h2>Family</h2>
      <div id="family"></div>
      <h2>Notes</h2>
      <form id="note-form" class="toolbar"><textarea name="note" rows="2" placeholder="Add a note about this client" required></textarea><button>Save note</button></form>
      <div class="notes">${(notes.data || []).map((n) => `<div class="note">${esc(n.note)}<br><small>${esc(n.created_by || "")} · ${new Date(n.created_at).toLocaleString("en-IN")}</small></div>`).join("") || '<p class="muted">No notes yet.</p>'}</div>`;

    $("#note-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const note = new FormData(e.target).get("note").trim();
      if (!note) return;
      const { error } = await sb.from("client_notes").insert({ pan, note, created_by: me.email });
      if (error) return alert("The note was not saved: " + error.message);
      client(pan);
    });
    familyPicker(pan, fam.data);
  }
  const fact = (k, v, html) => `<div><dt>${esc(k)}</dt><dd>${html ? v || "–" : esc(v || "–")}</dd></div>`;
  const uniq = (a) => [...new Set(a.map((x) => String(x).trim()).filter(Boolean))];

  function arnHistoryTable(hist, fs) {
    if (!hist.length) return `<div class="panel"><div class="empty">No transactions loaded yet, so the ARN history is empty.</div></div>`;
    const names = new Map(fs.map((f) => [f.registrar + "|" + f.folio_no + "|" + f.product_code, f.scheme_name]));
    const years = [...new Set(hist.map((h) => h.year))].sort();
    const rows = new Map();
    hist.forEach((h) => {
      const k = h.registrar + "|" + h.folio_no + "|" + h.product_code;
      if (!rows.has(k)) rows.set(k, { k, folio: h.folio_no, scheme: names.get(k) || h.product_code, byYear: {} });
      const y = rows.get(k).byYear;
      (y[h.year] = y[h.year] || []).push(h);
    });
    const legendArns = [...new Set(hist.map((h) => h.arn || "Other"))];
    const head = `<th>Scheme</th><th>Folio</th>` + years.map((y) => `<th class="num">${y}</th>`).join("");
    const body = [...rows.values()].map((r) => `<tr><td>${esc(r.scheme)}</td><td>${esc(r.folio)}</td>` +
      years.map((y) => {
        const cells = r.byYear[y] || [];
        if (!cells.length) return `<td class="year"></td>`;
        const title = cells.map((c) => `${c.arn || "no ARN"}: ${c.transactions} transactions`).join("\n");
        return `<td class="year"><div class="cell" title="${esc(title)}">${cells.map((c) => `<span style="background:${arnColor(c.arn)}"></span>`).join("")}</div></td>`;
      }).join("") + `</tr>`).join("");
    return `<div class="legend">${legendArns.map((a) => `<span><i style="background:${arnColor(a)}"></i>${esc(a)}</span>`).join("")}</div>
      <div class="panel map"><div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
  }

  async function familyPicker(pan, current) {
    const box = $("#family");
    const { data: fams } = await sb.from("families").select("id,name").order("name");
    box.innerHTML = `<form class="toolbar" id="fam-form">
      <select name="fid" aria-label="Family">
        <option value="">Not in a family</option>
        ${(fams || []).map((f) => `<option value="${f.id}" ${current && current.family_id === f.id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}
        <option value="new">New family…</option>
      </select><button>Save family</button></form>`;
    $("#fam-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      let fid = new FormData(e.target).get("fid");
      if (fid === "new") {
        const name = prompt("Family name");
        if (!name) return;
        const { data, error } = await sb.from("families").insert({ name }).select().single();
        if (error) return alert(error.message);
        fid = data.id;
      }
      const res = fid
        ? await sb.from("family_members").upsert({ pan, family_id: Number(fid) })
        : await sb.from("family_members").delete().eq("pan", pan);
      if (res.error) return alert(res.error.message);
      client(pan);
    });
  }

  async function family(id) {
    const [{ data: fam }, { data: members }] = await Promise.all([
      sb.from("families").select("*").eq("id", id).single(),
      sb.from("family_members").select("pan").eq("family_id", id),
    ]);
    const pans = (members || []).map((m) => m.pan);
    const { data: cl } = pans.length ? await sb.from("clients").select("*").in("pan", pans) : { data: [] };
    const mv = cl.reduce((s, c) => s + (c.market_value || 0), 0);
    const cv = cl.reduce((s, c) => s + (c.cost_value || 0), 0);
    view.innerHTML = `<h1 class="page">${esc(fam ? fam.name : "Family")}</h1><p class="sub">${cl.length} members</p>
      <div class="money"><div><div class="v">${rs(mv)}</div><div class="l">Market value</div></div>
      <div><div class="v">${rs(cv)}</div><div class="l">Cost value</div></div>
      <div><div class="v ${cls(mv - cv)}">${rs(mv - cv)}</div><div class="l">Gain (${pct(mv - cv, cv)})</div></div></div>
      ${table([
        { h: "Name", f: (r) => esc(r.inv_name) }, { h: "PAN", f: (r) => esc(r.pan) },
        { h: "Value", num: 1, f: (r) => rs(r.market_value) },
        { h: "Gain", num: 1, f: (r) => rs(r.market_value - r.cost_value), cls: (r) => cls(r.market_value - r.cost_value) },
      ], cl, { link: (r) => "#/client/" + r.pan })}`;
  }

  async function folio(registrar, folioNo) {
    // Transactions whose folio has no PAN on file land here.
    const { data } = await sb.from("folios").select("pan").eq("registrar", registrar).eq("folio_no", folioNo).limit(1);
    if (data && data[0] && data[0].pan) { location.hash = "#/client/" + data[0].pan; return; }
    view.innerHTML = `<h1 class="page">Folio ${esc(folioNo)}</h1><p class="sub">This folio is not in the investor master yet. Upload the ${regName(registrar)} investor master file to see the client.</p>`;
  }

  async function arnMap(params) {
    const arn = params.get("arn") || "";
    const from = Number(params.get("from") || new Date().getFullYear() - 5);
    view.innerHTML = `
      <h1 class="page">ARN map</h1>
      <p class="sub">Which ARN each folio and scheme was transacted under, year by year. A folio that changed colour moved between ARNs.</p>
      <form class="toolbar" id="f">
        <select name="arn" aria-label="ARN"><option value="">All ARNs</option>
          ${Object.keys(cfg.ARN_COLORS).map((a) => `<option ${a === arn ? "selected" : ""}>${a}</option>`).join("")}</select>
        <label>From <select name="from" aria-label="From year">${range(2004, new Date().getFullYear()).map((y) => `<option ${y === from ? "selected" : ""}>${y}</option>`).join("")}</select></label>
        <button>Show</button>
      </form>
      <div id="summary"><p class="muted">Loading…</p></div><div id="moves"></div>`;
    $("#f").addEventListener("submit", (e) => {
      e.preventDefault();
      location.hash = "#/arn-map?" + new URLSearchParams(new FormData(e.target));
    });
    let q = () => {
      let x = sb.from("arn_history").select("registrar,folio_no,product_code,year,arn,transactions").gte("year", from);
      return arn ? x.eq("arn", arn) : x;
    };
    const rows = await all(q);
    const years = range(from, new Date().getFullYear());
    const arns = [...new Set(rows.map((r) => r.arn || "Other"))].sort();
    const stat = {};
    rows.forEach((r) => {
      const k = (r.arn || "Other") + "|" + r.year;
      stat[k] = stat[k] || { folios: new Set(), tx: 0 };
      stat[k].folios.add(r.registrar + r.folio_no + r.product_code);
      stat[k].tx += r.transactions;
    });
    $("#summary").innerHTML = `<h2>Folio-schemes with transactions, by year</h2>` + table(
      [{ h: "ARN", f: (a) => arnTag(a) || esc(a) }].concat(years.map((y) => ({ h: String(y), num: 1, f: (a) => (stat[a + "|" + y] ? inr.format(stat[a + "|" + y].folios.size) : "") }))),
      arns, { empty: "No transactions in this period." });

    // Folios that appear under more than one ARN across the years.
    if (!arn) {
      const per = new Map();
      rows.forEach((r) => {
        const k = r.registrar + "|" + r.folio_no + "|" + r.product_code;
        if (!per.has(k)) per.set(k, []);
        per.get(k).push(r);
      });
      const moved = [...per.entries()].filter(([, v]) => new Set(v.map((x) => x.arn)).size > 1).slice(0, 300);
      const keys = moved.map(([k]) => k.split("|")[1]);
      const { data: fo } = keys.length ? await sb.from("folios").select("registrar,folio_no,product_code,inv_name,pan,scheme_name").in("folio_no", keys) : { data: [] };
      const info = new Map((fo || []).map((f) => [f.registrar + "|" + f.folio_no + "|" + f.product_code, f]));
      $("#moves").innerHTML = `<h2>Folios that moved between ARNs (${moved.length})</h2>` + table([
        { h: "Client", f: ([k]) => esc((info.get(k) || {}).inv_name || "") },
        { h: "Scheme", f: ([k]) => esc((info.get(k) || {}).scheme_name || k.split("|")[2]) },
        { h: "Folio", f: ([k]) => esc(k.split("|")[1]) },
        { h: "ARN by year", f: ([, v]) => v.sort((a, b) => a.year - b.year).map((x) => `${x.year} ${arnTag(x.arn) || "no ARN"}`).join(" ") },
      ], moved, { link: ([k]) => ((info.get(k) || {}).pan ? "#/client/" + info.get(k).pan : "#/arn-map"), empty: "No folio changed ARN in this period." });
    }
  }
  const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);

  async function followUp(params) {
    const only = params.get("only") || "";
    const cols = { no_nominee: "No nominee", no_email: "No email", no_mobile: "No mobile", no_dob: "No date of birth", no_pan: "No PAN" };
    view.innerHTML = `<h1 class="page">Missing details</h1>
      <p class="sub">Folios that hold units but are missing a nominee, email, mobile, date of birth or PAN.</p>
      <div class="toolbar">${Object.entries(cols).map(([k, v]) => `<a href="#/follow-up?only=${k}" ${only === k ? 'aria-current="page"' : ""}>${v}</a>`).join(" · ")} · <a href="#/follow-up">All</a></div>
      <div id="list"><p class="muted">Loading…</p></div>`;
    const rows = await all(() => {
      let q = sb.from("missing_details").select("*").order("inv_name");
      return only ? q.eq(only, true) : q;
    });
    $("#list").innerHTML = table([
      { h: "Client", f: (r) => esc(r.inv_name) },
      { h: "Scheme", f: (r) => esc(r.scheme_name) },
      { h: "Folio", f: (r) => esc(r.folio_no) },
      { h: "ARN", f: (r) => arnTag(r.arn) },
      { h: "Missing", f: (r) => Object.keys(cols).filter((k) => r[k]).map((k) => `<span class="tag">${cols[k].replace("No ", "")}</span>`).join(" ") },
    ], rows, { link: (r) => (r.pan ? "#/client/" + r.pan : "#/follow-up"), empty: "Every active folio has these details." });
  }

  async function uploads() {
    view.innerHTML = `<h1 class="page">Upload files</h1>
      <p class="sub">Add CAMS or KFintech report files here: zip files as they arrive by email, or the unzipped .xls, .xlsx or .csv. Upload the original files, not copies saved as Google Sheets, because Sheets changes the dates. The importer runs every two hours.</p>
      <label class="drop" id="drop">Drop files here or <u>choose files</u>
        <input type="file" id="files" multiple hidden accept=".zip,.csv,.xls,.xlsx,.txt"></label>
      <div id="up"></div>
      <h2>Import history</h2><div id="log"><p class="muted">Loading…</p></div>`;
    const drop = $("#drop"), input = $("#files");
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files); });
    input.addEventListener("change", () => send(input.files));
    async function send(files) {
      const out = $("#up");
      for (const f of files) {
        const safe = new Date().toISOString().slice(0, 10) + "_manual_" + f.name.replace(/[^A-Za-z0-9._-]/g, "_");
        const line = document.createElement("p");
        line.textContent = "Uploading " + f.name + "…";
        out.appendChild(line);
        const { error } = await sb.storage.from("inbox").upload("incoming/" + safe, f, { upsert: true });
        line.textContent = error ? `${f.name}: not uploaded (${error.message})` : `${f.name}: uploaded. It will be imported on the next run.`;
        line.className = error ? "loss" : "gain";
      }
    }
    const { data, error } = await sb.from("import_log").select("*").order("id", { ascending: false }).limit(100);
    if (error) return fail(error);
    $("#log").innerHTML = table([
      { h: "When", f: (r) => new Date(r.started_at).toLocaleString("en-IN") },
      { h: "File", f: (r) => esc(r.file_name) },
      { h: "Type", f: (r) => esc((r.report_type || "").split(",").map((t) => REPORT_NAMES[t] || t).join(", ")) },
      { h: "Status", f: (r) => `<span class="status-${esc(r.status)}">${esc(r.status)}</span>` },
      { h: "Rows", num: 1, f: (r) => r.rows_read ?? "" },
      { h: "New", num: 1, f: (r) => r.inserted ?? "" },
      { h: "Updated", num: 1, f: (r) => r.updated ?? "" },
      { h: "Already there", num: 1, f: (r) => r.skipped ?? "" },
      { h: "Note", f: (r) => esc(r.message || "") },
    ], data, { empty: "No imports yet." });
  }

  const REPORT_NAMES = {
    cams_master: "CAMS investor details", cams_txn: "CAMS transactions",
    kfin_master: "KFintech investor master", kfin_txn: "KFintech transactions", kfin_aum: "KFintech AUM",
  };

  /* ---------- routing and sign-in ---------- */
  const routes = [
    [/^\/?$/, () => home(), "home"],
    [/^\/clients$/, (m, p) => clients(p), "clients"],
    [/^\/client\/([A-Za-z0-9]+)$/, (m) => client(m[1]), "clients"],
    [/^\/family\/(\d+)$/, (m) => family(m[1]), "clients"],
    [/^\/folio\/(\w+)\/([\w-]+)$/, (m) => folio(m[1], m[2]), "clients"],
    [/^\/arn-map$/, (m, p) => arnMap(p), "arn-map"],
    [/^\/follow-up$/, (m, p) => followUp(p), "follow-up"],
    [/^\/uploads$/, () => uploads(), "uploads"],
  ];
  async function route() {
    const [path, qs] = (location.hash.slice(1) || "/").split("?");
    for (const [re, fn, nav] of routes) {
      const m = path.match(re);
      if (m) {
        document.querySelectorAll(".side a").forEach((a) =>
          a.dataset.nav === nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
        try { await fn(m, new URLSearchParams(qs || "")); } catch (e) { fail(e); }
        view.focus({ preventScroll: true });
        return;
      }
    }
    view.innerHTML = `<h1 class="page">Page not found</h1><p class="sub"><a href="#/">Go to the overview</a></p>`;
  }

  async function start(session) {
    if (!session) {
      $("#app").hidden = true;
      $("#signin").hidden = false;
      return;
    }
    me = session.user;
    const { data: allowed } = await sb.rpc("is_app_user");
    if (!allowed) {
      $("#signin").hidden = false;
      $("#app").hidden = true;
      $("#signin-msg").textContent = `${me.email} is not on the CRM's list of users. Ask the owner to add it.`;
      await sb.auth.signOut();
      return;
    }
    $("#who").textContent = me.email;
    $("#signin").hidden = true;
    $("#app").hidden = false;
    route();
  }

  $("#signin-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#email").value.trim();
    const msg = $("#signin-msg");
    msg.textContent = "Sending…";
    const { error } = await sb.auth.signInWithOtp({
      email, options: { shouldCreateUser: false, emailRedirectTo: location.origin + location.pathname },
    });
    msg.textContent = error ? "The link was not sent: " + error.message
      : "Check your email for the sign-in link. You can close this tab.";
  });
  $("#signout").addEventListener("click", async () => { await sb.auth.signOut(); location.hash = ""; });
  window.addEventListener("hashchange", () => { if (me) route(); });

  sb.auth.getSession().then(({ data }) => start(data.session));
  sb.auth.onAuthStateChange((event, session) => {
    if (event === "SIGNED_IN" && !me) start(session);
    if (event === "SIGNED_OUT") { me = null; start(null); }
  });
})();
