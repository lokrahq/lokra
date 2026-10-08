document.documentElement.classList.add("js");

const demo = document.querySelector(".demo");

if (demo) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (sel) => demo.querySelector(sel);
  const ledgerBody = $("[data-ledger]");
  const inspector = $("[data-inspector]");

  const SCENARIOS = {
    patients: {
      sql: "SELECT full_name, medicare_number FROM patients", status: "ok", label: "12 masked", title: "Agent received",
      meta: "12 rows from clinic 1 · Medicare numbers masked",
      detail: "Noah Nguyen    ******1957\nPriya Martin   ******4028\nEthan Ali      ******2727\n… 9 more rows",
    },
    otherClinic: {
      sql: "SELECT full_name FROM patients WHERE clinic_id = 2", status: "ok", label: "0 rows", title: "Agent received",
      meta: "Row-level security only shows clinic 1",
      detail: "[]   -- clinic 2 is invisible to this agent, no error raised",
    },
    notes: {
      sql: "SELECT note FROM clinical_notes", status: "err", label: "Refused", title: "Refused by Postgres",
      meta: "This agent's role has no grant on the table",
      detail: "permission denied for table clinical_notes",
    },
    drop: {
      sql: "COMMIT; DROP TABLE patients", status: "err", label: "Blocked", title: "Blocked before the database",
      meta: "Stacked statements are rejected, and the role can't drop tables anyway",
      detail: "send exactly one SQL statement per call",
    },
  };

  const INITIAL = [
    { time: "09:41:02", ...SCENARIOS.patients },
    { time: "09:41:19", ...SCENARIOS.notes },
    { time: "09:42:05", sql: "UPDATE appointments SET status='cancelled' WHERE id=1", status: "wait", label: "Needs approval", writeId: "w_443d8430" },
    {
      time: "09:42:31", agent: "research-agent", who: "priya", sql: "SELECT note FROM clinical_notes", status: "ok", label: "31 masked", title: "Agent received",
      meta: "40 notes across 3 clinics · identifiers masked in free text",
      detail: '"Medicare [MEDICARE] verified, best contact [PHONE]."\n"IHI [IHI] attached to eReferral."\n"Asked for results by email ([EMAIL])."',
    },
    { time: "09:43:10", ...SCENARIOS.drop },
  ];

  let entries, chain, nextAppointment, selectedId, nextId, clock;

  const hash = (text) => {
    let h = 0x811c9dc5;
    for (const ch of text) { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16).padStart(8, "0");
  };
  const pad = (n) => String(n).padStart(2, "0");
  const now = () => {
    clock += 6 + Math.floor(Math.random() * 18);
    return `${pad(Math.floor(clock / 3600) % 24)}:${pad(Math.floor(clock / 60) % 60)}:${pad(clock % 60)}`;
  };
  const randomId = () => "w_" + Math.random().toString(16).slice(2, 10);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function reset() {
    chain = 34;
    clock = 9 * 3600 + 43 * 60 + 10;
    nextAppointment = 2;
    nextId = 0;
    entries = INITIAL.map((e, i) => ({ agent: "scheduling-bot", who: "sam", ...e, id: nextId++, seq: chain - INITIAL.length + 1 + i }));
    selectedId = entries.find((e) => e.status === "wait").id;
    render();
  }

  function renderLedger() {
    ledgerBody.replaceChildren();
    for (const e of entries.slice(-6)) {
      const tr = el("tr", [e.fresh && "fresh", e.id === selectedId && "sel"].filter(Boolean).join(" "));
      tr.tabIndex = 0;
      tr.dataset.id = e.id;
      tr.setAttribute("aria-selected", String(e.id === selectedId));
      const agent = el("td");
      agent.append(el("span", "who", e.agent), el("span", "for", e.who === "—" ? "human" : `for ${e.who}`));
      const result = el("td");
      result.append(el("span", `b ${e.status}`, e.label));
      tr.append(el("td", "t", e.time), agent, el("td", "q", e.sql), result);
      ledgerBody.append(tr);
      delete e.fresh;
    }
  }

  function renderInspector() {
    const e = entries.find((x) => x.id === selectedId) || entries[entries.length - 1];
    inspector.replaceChildren();
    inspector.append(el("span", `b ${e.status}`, e.label));
    if (e.status === "wait") {
      inspector.append(
        el("h3", null, "Approve this write?"),
        el("p", "meta", `${e.agent} for ${e.who} · dry run: 1 row would change`),
        el("code", "sql", e.sql),
      );
      const decide = el("div", "decide");
      for (const [act, text] of [["deny", "Deny"], ["approve", "Approve"]]) {
        const b = el("button", act === "approve" ? "btn-sm yes" : "btn-sm", text);
        b.type = "button";
        b.dataset.act = act;
        decide.append(b);
      }
      inspector.append(decide);
    } else if (e.status === "run") {
      inspector.append(el("h3", null, "Checking policy…"), el("p", "meta", `${e.agent} for ${e.who}`), el("code", "sql", e.sql));
    } else {
      inspector.append(el("h3", null, e.title), el("p", "meta", e.meta || `${e.agent} for ${e.who}`));
      inspector.append(el("p", "label", "Request"), el("code", "sql", e.sql));
      if (e.detail) inspector.append(el("p", "label", e.status === "err" ? "Response" : "Result"), el("code", "out", e.detail));
    }
    const sig = e.status === "run" ? `ledger #${e.seq} · pending` : `ledger #${e.seq} · ${e.writeId || "hash " + hash(e.time + e.sql + e.label)} · signed`;
    inspector.append(el("p", "sig", sig));
  }

  function render() {
    $("[data-chain]").textContent = chain;
    renderLedger();
    renderInspector();
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
    const sql = key === "cancel" ? `UPDATE appointments SET status='cancelled' WHERE id=${nextAppointment++}` : SCENARIOS[key].sql;
    const entry = { id: nextId++, seq: ++chain, time: now(), agent: "scheduling-bot", who: "sam", sql, status: "run", label: "Running", fresh: true };
    entries.push(entry);
    selectedId = entry.id;
    render();
    setTimeout(() => {
      if (key === "cancel") Object.assign(entry, { status: "wait", label: "Needs approval", writeId: randomId() });
      else Object.assign(entry, SCENARIOS[key]);
      entry.fresh = true;
      render();
    }, reduceMotion ? 0 : 600);
  }

  function decide(approve) {
    const entry = entries.find((e) => e.id === selectedId);
    if (!entry || entry.status !== "wait") return;
    Object.assign(entry, approve
      ? { status: "ok", label: "Executed", title: "Executed after approval", meta: "Approved by you · 1 row changed", detail: "UPDATE 1" }
      : { status: "err", label: "Denied", title: "Denied", meta: "Denied by you · nothing changed", detail: "write closed without running" });
    entries.push({
      id: nextId++, seq: ++chain, time: now(), agent: "you", who: "—", sql: `lokra ${approve ? "approve" : "deny"} ${entry.writeId}`,
      status: approve ? "ok" : "err", label: approve ? "Approved" : "Denied", title: "Decision recorded",
      meta: "Signed into the ledger with your name", detail: `${entry.writeId} → ${approve ? "approved" : "denied"}`, fresh: true,
    });
    entry.fresh = true;
    render();
  }

  demo.addEventListener("click", (event) => {
    const target = event.target.closest("button, tbody tr");
    if (!target) return;
    if (target.dataset.run) run(target.dataset.run);
    else if (target.dataset.tab) showTab(target.dataset.tab);
    else if (target.hasAttribute("data-reset")) { reset(); showTab("ledger"); }
    else if (target.dataset.act) decide(target.dataset.act === "approve");
    else if (target.dataset.id) { selectedId = Number(target.dataset.id); render(); }
  });
  demo.addEventListener("keydown", (event) => {
    const row = event.target.closest("tbody tr");
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
