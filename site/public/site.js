document.documentElement.classList.add("js");

const demo = document.querySelector(".demo");

if (demo) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (sel) => demo.querySelector(sel);
  const ledgerBody = $("[data-ledger]");
  const card = $("[data-approve]");

  const SCENARIOS = {
    patients: {
      sql: "SELECT full_name, medicare_number FROM patients", rows: "12", status: "ok", label: "12 masked",
      detail: '{ "rows": [["Noah Nguyen", "******1957"],\n           ["Priya Martin", "******4028"], …],\n  "row_count": 12,\n  "masked": { "column:last4": 12 } }',
    },
    otherClinic: {
      sql: "SELECT full_name FROM patients WHERE clinic_id = 2", rows: "0", status: "ok", label: "0 rows",
      detail: "Row-level security: scheduling-bot can only see clinic 1.\nPostgres returned no rows for clinic 2, without an error.",
    },
    notes: {
      sql: "SELECT note FROM clinical_notes", rows: "—", status: "err", label: "Refused by Postgres",
      detail: "database refused the query: permission denied for table clinical_notes\nThe scheduling-bot role has no grant on this table.",
    },
    drop: {
      sql: "COMMIT; DROP TABLE patients", rows: "—", status: "err", label: "Blocked",
      detail: "send exactly one SQL statement per call\nEven if it got through, Postgres would refuse: must be owner of table patients.",
    },
  };

  const pendingDetail = (id) => `Dry run as scheduling-bot: would affect 1 row.\nQueued as ${id}. Nothing changes until a person approves.`;

  const INITIAL = [
    { time: "09:41:02", ...SCENARIOS.patients },
    { time: "09:41:19", ...SCENARIOS.notes },
    { time: "09:42:05", sql: "UPDATE appointments SET status='cancelled' WHERE id=1", rows: "1", status: "wait", label: "Awaiting approval", writeId: "w_443d8430", detail: pendingDetail("w_443d8430") },
    {
      time: "09:42:31", agent: "research-agent", who: "priya", sql: "SELECT note FROM clinical_notes", rows: "40", status: "ok", label: "31 masked",
      detail: '"Pt called to confirm details. Medicare [MEDICARE] verified, best contact [PHONE]."\n"Referral letter sent. IHI [IHI] attached to eReferral."\n"Patient asked for results by email ([EMAIL])."',
    },
    { time: "09:43:10", ...SCENARIOS.drop },
  ];

  let entries, chain, nextAppointment, openId, nextId, cardTimer, clock;

  const hash = (text) => {
    let h = 0x811c9dc5;
    for (const ch of text) { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16).padStart(8, "0");
  };
  const now = () => {
    clock += 6 + Math.floor(Math.random() * 18);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(clock / 3600) % 24)}:${pad(Math.floor(clock / 60) % 60)}:${pad(clock % 60)}`;
  };
  const randomId = () => "w_" + Math.random().toString(16).slice(2, 10);

  function reset() {
    chain = 34;
    clock = 9 * 3600 + 43 * 60 + 10;
    nextAppointment = 2;
    openId = null;
    nextId = 0;
    entries = INITIAL.map((e, i) => ({ agent: "scheduling-bot", who: "sam", ...e, id: nextId++, seq: chain - INITIAL.length + 1 + i }));
    render();
  }

  function cell(text, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = text;
    return td;
  }

  function renderLedger() {
    ledgerBody.replaceChildren();
    for (const e of entries.slice(-7)) {
      const tr = document.createElement("tr");
      tr.className = "entry" + (e.fresh ? " fresh" : "");
      tr.tabIndex = 0;
      tr.dataset.id = e.id;
      tr.setAttribute("aria-expanded", String(openId === e.id));
      const result = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `res ${e.status}`;
      badge.textContent = e.label;
      result.append(badge);
      tr.append(cell(e.time, "mono"), cell(e.agent), cell(e.who), cell(e.sql, "q"), cell(e.rows), result);
      ledgerBody.append(tr);
      if (openId === e.id) {
        const row = document.createElement("tr");
        row.className = "detail";
        const td = document.createElement("td");
        td.colSpan = 6;
        td.textContent = `${e.detail || "Running…"}\n\nledger #${e.seq} · hash ${hash(e.time + e.sql + e.label)} · signed`;
        row.append(td);
        ledgerBody.append(row);
      }
      delete e.fresh;
    }
  }

  function renderPending() {
    const pending = entries.filter((e) => e.status === "wait");
    const badge = $("[data-pending-count]");
    badge.textContent = pending.length;
    badge.toggleAttribute("data-zero", pending.length === 0);
    const list = $("[data-pending-list]");
    list.replaceChildren();
    if (!pending.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No writes waiting. Try “Cancel an appointment”.";
      list.append(li);
    }
    for (const e of pending) {
      const li = document.createElement("li");
      const info = document.createElement("div");
      const code = document.createElement("code");
      code.textContent = e.sql;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${e.agent} for ${e.who} · would affect ${e.rows} row · ${e.writeId}`;
      info.append(code, meta);
      const actions = document.createElement("div");
      actions.className = "row";
      for (const [act, text] of [["deny", "Deny"], ["approve", "Approve"]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = text;
        b.dataset.act = act;
        b.dataset.id = e.id;
        if (act === "approve") b.className = "yes";
        actions.append(b);
      }
      li.append(info, actions);
      list.append(li);
    }
    return pending;
  }

  function renderCard(pending) {
    if (card.classList.contains("done") || card.classList.contains("denied")) return;
    const latest = pending[pending.length - 1];
    const sql = $("[data-approve-sql]");
    card.classList.toggle("idle", !latest);
    if (!latest) {
      $("[data-approve-title]").textContent = "No writes waiting";
      $("[data-approve-meta]").textContent = "Run “Cancel an appointment” to see an approval.";
      sql.hidden = true;
      return;
    }
    $("[data-approve-title]").textContent = "Approve write?";
    $("[data-approve-meta]").textContent = `${latest.agent} for ${latest.who} · would affect ${latest.rows} row`;
    sql.hidden = false;
    sql.textContent = latest.sql;
    card.dataset.id = latest.id;
  }

  function render() {
    $("[data-chain]").textContent = chain;
    renderLedger();
    renderCard(renderPending());
  }

  function showTab(name) {
    demo.querySelectorAll("[data-tab]").forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("on", on);
      t.setAttribute("aria-selected", String(on));
    });
    demo.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  }

  function run(key) {
    showTab("ledger");
    const entry = { id: nextId++, seq: ++chain, time: now(), agent: "scheduling-bot", who: "sam", sql: "", rows: "…", status: "run", label: "Running…", fresh: true };
    if (key === "cancel") {
      entry.sql = `UPDATE appointments SET status='cancelled' WHERE id=${nextAppointment++}`;
    } else {
      entry.sql = SCENARIOS[key].sql;
    }
    entries.push(entry);
    render();
    setTimeout(() => {
      if (key === "cancel") {
        entry.writeId = randomId();
        Object.assign(entry, { rows: "1", status: "wait", label: "Awaiting approval", detail: pendingDetail(entry.writeId) });
      } else {
        const { sql, ...outcome } = SCENARIOS[key];
        Object.assign(entry, outcome);
      }
      entry.fresh = true;
      render();
    }, reduceMotion ? 0 : 550);
  }

  function decide(id, approve) {
    const entry = entries.find((e) => e.id === Number(id));
    if (!entry || entry.status !== "wait") return;
    if (approve) {
      Object.assign(entry, { status: "ok", label: "Executed · 1 row", detail: `Approved by you, then executed as scheduling-bot.\n1 row changed. ${entry.writeId} is closed.` });
    } else {
      Object.assign(entry, { status: "err", label: "Denied", detail: `Denied by you. Nothing was changed.\n${entry.writeId} is closed.` });
    }
    entries.push({
      id: nextId++, seq: ++chain, time: now(), agent: "you", who: "—", sql: `lokra ${approve ? "approve" : "deny"} ${entry.writeId}`,
      rows: "—", status: approve ? "ok" : "err", label: approve ? "Approved" : "Denied", fresh: true,
      detail: `Decision recorded in the signed ledger with your name, the time and ${entry.writeId}.`,
    });
    clearTimeout(cardTimer);
    card.classList.remove("idle", "done", "denied");
    card.classList.add(approve ? "done" : "denied");
    $("[data-approve-title]").textContent = approve ? "Approved · 1 row changed" : "Denied · nothing changed";
    $("[data-approve-meta]").textContent = "Logged to the ledger with your name.";
    $("[data-approve-sql]").hidden = true;
    card.querySelector(".row").style.display = "none";
    render();
    cardTimer = setTimeout(() => {
      card.classList.remove("done", "denied");
      card.querySelector(".row").style.display = "";
      render();
    }, reduceMotion ? 1500 : 2200);
  }

  demo.addEventListener("click", (event) => {
    const target = event.target.closest("button, tr.entry");
    if (!target) return;
    if (target.dataset.run) run(target.dataset.run);
    else if (target.dataset.tab) showTab(target.dataset.tab);
    else if (target.hasAttribute("data-reset")) { clearTimeout(cardTimer); card.classList.remove("done", "denied"); card.querySelector(".row").style.display = ""; reset(); showTab("ledger"); }
    else if (target.dataset.act) decide(target.dataset.id || card.dataset.id, target.dataset.act === "approve");
    else if (target.matches("tr.entry")) { const id = Number(target.dataset.id); openId = openId === id ? null : id; renderLedger(); }
  });
  demo.addEventListener("keydown", (event) => {
    const row = event.target.closest("tr.entry");
    if (row && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); row.click(); }
  });

  reset();
}


const form = document.querySelector(".waitlist");
const status = document.querySelector(".status");

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = form.email.value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      status.textContent = "That email doesn't look right.";
      form.email.focus();
      return;
    }
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "";
    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email, website: form.website.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Something went wrong.");
      form.hidden = true;
      status.textContent = "You're on the list. We'll email you when the hosted version is ready.";
    } catch (err) {
      status.textContent = err.message || "Something went wrong. Try again in a moment.";
      button.disabled = false;
    }
  });
  form.email.addEventListener("input", () => { status.textContent = ""; });
}
