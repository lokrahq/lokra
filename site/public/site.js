document.documentElement.classList.add("js");

const demo = document.querySelector(".demo");

if (demo) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (sel) => demo.querySelector(sel);
  const ledgerBody = $("[data-ledger]");
  const inspector = $("[data-inspector]");

  const SCENARIOS = {
    patients: {
      prompt: "Who's booked today?",
      why: "medicare_number is masked by policy before the agent sees it, and only clinic 1's rows come back.",
      sql: "SELECT full_name, medicare_number FROM patients", status: "ok", label: "12 masked", title: "Agent received",
      meta: "12 rows from clinic 1 · Medicare numbers masked",
      detail: "Noah Nguyen    ******1957\nPriya Martin   ******4028\nEthan Ali      ******2727\n… 9 more rows",
    },
    otherClinic: {
      prompt: "Show Westside's patients",
      why: "Row-level security tied to this agent's role hides other clinics. Postgres returns nothing instead of an error, so there's nothing to leak.",
      sql: "SELECT full_name FROM patients WHERE clinic_id = 2", status: "ok", label: "0 rows", title: "Agent received",
      meta: "Row-level security only shows clinic 1",
      detail: "[]   -- clinic 2 is invisible to this agent, no error raised",
    },
    notes: {
      prompt: "Summarise Noah's notes",
      why: "The scheduling-bot role has no grant on clinical_notes, so Postgres itself refused. Lokra didn't have to guess.",
      sql: "SELECT note FROM clinical_notes", status: "err", label: "Refused", title: "Refused by Postgres",
      meta: "This agent's role has no grant on the table",
      detail: "permission denied for table clinical_notes",
    },
    drop: {
      prompt: "Drop the patients table",
      why: "Only one statement per call, sent as a prepared statement. Even if it slipped through, this role can't drop tables.",
      sql: "COMMIT; DROP TABLE patients", status: "err", label: "Blocked", title: "Blocked before the database",
      meta: "Stacked statements are rejected, and the role can't drop tables anyway",
      detail: "send exactly one SQL statement per call",
    },
  };

  const INITIAL = [
    { time: "09:41:02", ...SCENARIOS.patients },
    { time: "09:41:19", ...SCENARIOS.notes },
    { time: "09:42:05", prompt: "Cancel Noah's appointment on Monday", sql: "UPDATE appointments SET status='cancelled' WHERE id=1", status: "wait", label: "Needs approval", writeId: "w_443d8430" },
    {
      time: "09:42:31", agent: "research-agent", who: "priya", prompt: "Find notes that mention a Medicare number",
      why: "Identifiers inside free text are detected and masked too: Medicare, IHI, email and phone.",
      sql: "SELECT note FROM clinical_notes", status: "ok", label: "31 masked", title: "Agent received",
      meta: "40 notes across 3 clinics · identifiers masked in free text",
      detail: '"Medicare [MEDICARE] verified, best contact [PHONE]."\n"IHI [IHI] attached to eReferral."\n"Asked for results by email ([EMAIL])."',
    },
    { time: "09:43:10", ...SCENARIOS.drop },
  ];

  const WRITE_WHY = "Every write is dry-run first and waits for a person. Nothing changes until someone approves.";
  const chips = [...demo.querySelectorAll("[data-run]")];
  const playButton = $("[data-play]");
  let entries, chain, nextAppointment, selectedId, nextId, clock, playing = false;

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

  function reset(clean = false) {
    chain = 34;
    clock = 9 * 3600 + 43 * 60 + 10;
    nextAppointment = 2;
    nextId = 0;
    entries = clean ? [] : INITIAL.map((e, i) => ({ agent: "scheduling-bot", who: "sam", ...e, id: nextId++, seq: chain - INITIAL.length + 1 + i }));
    selectedId = clean ? null : entries.find((e) => e.status === "wait").id;
    demo.querySelector('[data-run="cancel"]').textContent = "Cancel appointment #2";
    render();
  }

  function renderLedger() {
    ledgerBody.replaceChildren();
    if (!entries.length) {
      const tr = el("tr", "empty-row");
      const td = el("td", "empty", "No requests yet. The walkthrough is about to start.");
      td.colSpan = 4;
      tr.append(td);
      ledgerBody.append(tr);
    }
    for (const e of entries.slice(-6)) {
      const tr = el("tr", [e.fresh && "fresh", e.id === selectedId && "sel"].filter(Boolean).join(" "));
      tr.tabIndex = 0;
      tr.dataset.id = e.id;
      tr.setAttribute("aria-selected", String(e.id === selectedId));
      const agent = el("td");
      agent.append(el("span", "who", e.agent), el("span", "for", e.who === "—" ? "human" : `for ${e.who}`));
      const result = el("td");
      result.append(el("span", `b ${e.status}`, e.label));
      const q = el("td", "q", e.sql);
      q.title = e.sql;
      tr.append(el("td", "t", e.time), agent, q, result);
      ledgerBody.append(tr);
      delete e.fresh;
    }
  }

  function renderInspector() {
    const e = entries.find((x) => x.id === selectedId) || entries[entries.length - 1];
    if (!e) {
      inspector.replaceChildren(el("h3", null, "Waiting for the agent…"), el("p", "meta", "Each question appears here with what Lokra did and why."));
      return;
    }
    const ask = () => e.prompt && inspector.append(el("p", "ask", `“${e.prompt}”`));
    const why = (text) => {
      if (!text) return;
      const p = el("p", "why");
      p.append(el("b", null, "Why"), document.createTextNode(text));
      inspector.append(p);
    };
    inspector.replaceChildren();
    inspector.append(el("span", `b ${e.status}`, e.label));
    if (e.status === "wait") {
      inspector.append(el("h3", null, "Approve this write?"));
      ask();
      inspector.append(el("p", "meta", `${e.agent} for ${e.who} · dry run: 1 row would change`), el("code", "sql", e.sql));
      const decide = el("div", "decide");
      for (const [act, text] of [["deny", "Deny"], ["approve", "Approve"]]) {
        const b = el("button", act === "approve" ? "btn-sm yes" : "btn-sm", text);
        b.type = "button";
        b.dataset.act = act;
        decide.append(b);
      }
      inspector.append(decide);
      why(WRITE_WHY);
    } else if (e.status === "run") {
      inspector.append(el("h3", null, "Checking policy…"));
      ask();
      inspector.append(el("p", "meta", `${e.agent} for ${e.who}`), el("code", "sql", e.sql));
    } else {
      inspector.append(el("h3", null, e.title));
      ask();
      inspector.append(el("p", "meta", e.meta || `${e.agent} for ${e.who}`));
      inspector.append(el("p", "label", "Request"), el("code", "sql", e.sql));
      if (e.detail) inspector.append(el("p", "label", e.status === "err" ? "Response" : "Result"), el("code", "out", e.detail));
      why(e.why);
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
    const n = nextAppointment++;
    const sql = key === "cancel" ? `UPDATE appointments SET status='cancelled' WHERE id=${n}` : SCENARIOS[key].sql;
    const prompt = key === "cancel" ? `Cancel appointment #${n}` : SCENARIOS[key].prompt;
    if (key !== "cancel") nextAppointment--;
    const entry = { id: nextId++, seq: ++chain, time: now(), agent: "scheduling-bot", who: "sam", sql, prompt, status: "run", label: "Running", fresh: true };
    demo.querySelector(`[data-run="${key}"]`).classList.add("ran");
    demo.querySelector('[data-run="cancel"]').textContent = `Cancel appointment #${nextAppointment}`;
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
      ? { status: "ok", label: "Executed", title: "Executed after approval", meta: "Approved by you · 1 row changed", detail: "UPDATE 1",
          why: "It ran with the agent's own role, after your approval. The ledger links the request, the approval and the result." }
      : { status: "err", label: "Denied", title: "Denied", meta: "Denied by you · nothing changed", detail: "write closed without running",
          why: "Denied writes never reach the database, and the decision is still recorded." });
    entries.push({
      id: nextId++, seq: ++chain, time: now(), agent: "you", who: "—", sql: `lokra ${approve ? "approve" : "deny"} ${entry.writeId}`,
      status: approve ? "ok" : "err", label: approve ? "Approved" : "Denied", title: "Decision recorded",
      meta: "Signed into the ledger with your name", detail: `${entry.writeId} → ${approve ? "approved" : "denied"}`, fresh: true,
      why: "Every decision is signed into the ledger, so you can prove who approved what, and when.",
    });
    entry.fresh = true;
    render();
  }

  function setPlaying(on) {
    playing = on;
    chips.forEach((c) => { c.disabled = on; });
    playButton.disabled = on;
    playButton.querySelector("[data-play-label]").textContent = on ? "Playing…" : "▶\uFE0E Play";
    playButton.querySelector(".long").hidden = on;
  }

  function play() {
    if (playing) return;
    reset(true);
    chips.forEach((c) => c.classList.remove("ran"));
    showTab("ledger");
    setPlaying(true);
    const steps = ["patients", "otherClinic", "notes", "drop", "cancel"];
    const gap = reduceMotion ? 700 : 1500;
    steps.forEach((key, i) => setTimeout(() => {
      run(key);
      if (i === steps.length - 1) setTimeout(() => setPlaying(false), reduceMotion ? 0 : 650);
    }, i * gap));
  }

  demo.addEventListener("click", (event) => {
    const target = event.target.closest("button, tbody tr");
    if (!target) return;
    if (target.hasAttribute("data-play")) play();
    else if (playing && (target.dataset.run || target.hasAttribute("data-reset"))) return;
    else if (target.dataset.run) run(target.dataset.run);
    else if (target.dataset.tab) showTab(target.dataset.tab);
    else if (target.hasAttribute("data-reset")) { reset(); chips.forEach((c) => c.classList.remove("ran")); showTab("ledger"); }
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
