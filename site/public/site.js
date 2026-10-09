document.documentElement.classList.add("js");

const announce = document.querySelector("[data-announce]");
if (announce) {
  try { if (localStorage.getItem("lokra-announce-hidden") === "1") announce.hidden = true; } catch {}
  announce.querySelector("[data-dismiss]").addEventListener("click", () => {
    announce.hidden = true;
    try { localStorage.setItem("lokra-announce-hidden", "1"); } catch {}
  });
}

const demo = document.querySelector(".demo");

if (demo) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (sel) => demo.querySelector(sel);
  const ledgerBody = $("[data-ledger]");
  const inspector = $("[data-inspector]");
  const chips = [...demo.querySelectorAll("[data-run]")];
  const playButton = $("[data-play]");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const THINK = reduceMotion ? 0 : 900;
  const HOLD = reduceMotion ? 1800 : 3600;

  const DEFAULTS = { mask: true, notes: false, cancel: true };
  const APPOINTMENTS = [
    ["Noah", "Noah Nguyen", "Mon 10:00", "Monday"], ["Priya", "Priya Martin", "Tue 12:00", "Tuesday"],
    ["Ethan", "Ethan Ali", "Wed 09:00", "Wednesday"], ["Aisha", "Aisha Lee", "Thu 15:00", "Thursday"],
  ];
  const RULES = [
    { key: "mask", label: "Mask Medicare numbers", run: "patients" },
    { key: "notes", label: "Read clinical notes", run: "notes" },
    { key: "cancel", label: "Cancel appointments", run: "cancel" },
  ];
  const LOCKED = [["Human approval for writes", "Always on"], ["Drop or delete tables", "Always blocked"]];
  let policy = { ...DEFAULTS };
  let mode = "result";
  let showYaml = false;

  function evaluate(key, appt = 0) {
    if (key === "patients") {
      return policy.mask
        ? { prompt: "Who's booked today?", status: "ok", label: "12 masked", title: "What the agent sees",
            lines: ["Noah Nguyen   ******1957", "Priya Martin  ******4028", "Ethan Ali     ******2727"], note: "Medicare numbers are hidden by policy." }
        : { prompt: "Who's booked today?", status: "warn", label: "Exposed", title: "What the agent sees",
            lines: ["Noah Nguyen   4332 18195 7", "Priya Martin  2615 59402 8", "Ethan Ali     4192 83272 7"], note: "Masking is off, so real Medicare numbers reach the agent." };
    }
    if (key === "notes") {
      if (!policy.notes) return { prompt: "Read Noah's notes", status: "err", label: "Refused", title: "Refused by Postgres", note: "This agent has no access to clinical notes." };
      return policy.mask
        ? { prompt: "Read Noah's notes", status: "ok", label: "Allowed", title: "What the agent sees",
            lines: ['"Medicare [MEDICARE] verified."', '"Follow up in 6 weeks."'], note: "Notes are readable, identifiers inside them are still masked." }
        : { prompt: "Read Noah's notes", status: "warn", label: "Exposed", title: "What the agent sees",
            lines: ['"Medicare 4332 18195 7 verified."', '"Follow up in 6 weeks."'], note: "Notes are readable, identifiers and all." };
    }
    if (key === "drop") {
      return { prompt: "Drop the patients table", status: "err", label: "Blocked", title: "Blocked", note: "Destructive statements never reach the database." };
    }
    const [first, , , day] = APPOINTMENTS[appt % APPOINTMENTS.length];
    const prompt = `Cancel ${first}'s ${day} appointment`;
    return policy.cancel
      ? { prompt, status: "wait", label: "Needs approval", appt: appt % APPOINTMENTS.length }
      : { prompt, status: "err", label: "Refused", title: "Refused by Postgres", note: "This agent isn't allowed to change appointments." };
  }

  const isCustom = () => Object.keys(DEFAULTS).some((k) => policy[k] !== DEFAULTS[k]);

  function policyYaml() {
    const lines = ["agents:", "  scheduling-bot:", "    clinic_ids: [1]", "    read:", "      patients: [id, full_name, medicare_number]", '      appointments: "*"'];
    if (policy.notes) lines.push("      clinical_notes: [note]");
    lines.push(...(policy.cancel ? ["    write:", "      appointments: [update]"] : ["    write: {}"]));
    lines.push("", "masking:", ...(policy.mask ? ["  columns:", "    medicare_number: last4"] : ["  columns: {}"]));
    return lines.join("\n");
  }

  let entries, nextAppointment, selectedId, nextId, playing = false;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function reset(clean = false) {
    nextId = 0;
    nextAppointment = clean ? 0 : 1;
    entries = clean ? [] : [evaluate("patients"), evaluate("notes"), evaluate("cancel", 0), evaluate("drop")].map((e) => ({ ...e, id: nextId++ }));
    selectedId = clean ? null : (entries.find((e) => e.status === "wait") || entries[0]).id;
    chips.forEach((c) => c.classList.remove("active"));
    render();
  }

  function renderLedger() {
    ledgerBody.replaceChildren();
    if (!entries.length) {
      const tr = el("tr", "empty-row");
      const td = el("td", "empty", "Waiting for the agent…");
      td.colSpan = 2;
      tr.append(td);
      ledgerBody.append(tr);
    }
    for (const e of entries.slice(-5)) {
      const tr = el("tr", [e.fresh && "fresh", e.id === selectedId && "sel"].filter(Boolean).join(" "));
      tr.tabIndex = 0;
      tr.dataset.id = e.id;
      const result = el("td");
      result.append(el("span", `b ${e.status}`, e.label));
      tr.append(el("td", "ask-cell", e.prompt), result);
      ledgerBody.append(tr);
      delete e.fresh;
    }
  }

  function diffBox(e) {
    const [, name, when] = APPOINTMENTS[e.appt];
    const box = el("div", "diff");
    const head = el("div", "diff-head");
    head.append(el("b", null, name), el("span", null, when));
    const row = el("div", "diff-row");
    const change = el("span");
    change.append(el("span", "old", "booked"), el("span", "arrow", "→"), el("span", "new", "cancelled"));
    row.append(el("span", "k", "status"), change);
    box.append(head, row);
    return box;
  }

  function renderSwitcher() {
    const seg = el("div", "seg");
    for (const [value, text] of [["result", "Result"], ["policy", "Policy"]]) {
      const b = el("button", value === mode ? "on" : "", text);
      b.type = "button";
      b.dataset.mode = value;
      if (value === "policy" && isCustom()) b.append(el("span", "custom-dot"));
      seg.append(b);
    }
    inspector.append(seg);
  }

  function renderPolicy() {
    const head = el("div", "policy-head");
    head.append(el("h3", null, "scheduling-bot"));
    if (isCustom()) head.append(el("span", "b wait", "Custom"));
    const list = el("ul", "rules");
    for (const rule of RULES) {
      const li = el("li", "rule");
      const sw = el("button", "switch");
      sw.type = "button";
      sw.setAttribute("role", "switch");
      sw.setAttribute("aria-checked", String(policy[rule.key]));
      sw.setAttribute("aria-label", rule.label);
      sw.dataset.rule = rule.key;
      li.append(el("span", null, rule.label), sw);
      list.append(li);
    }
    for (const [label, fixed] of LOCKED) {
      const li = el("li", "rule locked");
      li.append(el("span", null, label), el("span", "fixed", fixed));
      list.append(li);
    }
    const foot = el("div", "policy-foot");
    const yamlButton = el("button", null, showYaml ? "Hide YAML" : "View YAML");
    yamlButton.type = "button";
    yamlButton.dataset.yaml = "";
    foot.append(yamlButton);
    if (isCustom()) {
      const resetButton = el("button", null, "Reset to defaults");
      resetButton.type = "button";
      resetButton.dataset.defaults = "";
      foot.append(resetButton);
    }
    inspector.append(head, list, foot);
    if (showYaml) inspector.append(el("code", "out yaml-out", policyYaml()));
  }

  function renderInspector() {
    const e = entries.find((x) => x.id === selectedId) || entries[entries.length - 1];
    inspector.replaceChildren();
    renderSwitcher();
    if (mode === "policy") {
      renderPolicy();
      return;
    }
    if (!e) {
      inspector.append(el("h3", null, "Ask the agent something"), el("p", "meta", "Or press Play to watch a walkthrough."));
      return;
    }
    if (e.status === "run") {
      inspector.append(el("span", "b run", "Checking"), el("h3", null, e.prompt));
      return;
    }
    if (e.status === "wait") {
      inspector.append(el("span", "b wait", "Needs approval"), el("h3", null, "Approve this change?"), el("p", "meta", "scheduling-bot · for sam"), diffBox(e));
      const decide = el("div", "decide");
      for (const [act, text] of [["deny", "Deny"], ["approve", "Approve"]]) {
        const button = el("button", act === "approve" ? "btn-sm yes" : "btn-sm", text);
        button.type = "button";
        button.dataset.act = act;
        decide.append(button);
      }
      inspector.append(decide);
      return;
    }
    if (e.decision) {
      const approved = e.decision === "approved";
      const head = el("div", "outcome");
      head.append(el("span", `mark ${approved ? "ok" : "err"}`, approved ? "✓" : "✕"), el("h3", null, approved ? "Approved" : "Denied"));
      inspector.append(head, el("p", "meta", approved ? "1 row changed, logged with your name." : "Nothing changed. Decision logged."));
      if (approved) inspector.append(diffBox(e));
      return;
    }
    inspector.append(el("span", `b ${e.status}`, e.label), el("h3", null, e.title));
    if (e.lines) inspector.append(el("code", "out", e.lines.join("\n")));
    inspector.append(el("p", "meta", e.note));
  }

  function render() {
    renderLedger();
    renderInspector();
  }

  async function run(key) {
    const base = key === "cancel" ? evaluate("cancel", nextAppointment++) : evaluate(key);
    const entry = { id: nextId++, prompt: base.prompt, status: "run", label: "Checking", fresh: true };
    entries.push(entry);
    selectedId = entry.id;
    render();
    await sleep(THINK);
    Object.assign(entry, base, { fresh: true });
    render();
  }

  async function decide(approve) {
    const entry = entries.find((e) => e.id === selectedId);
    if (!entry || entry.status !== "wait" || inspector.querySelector(".decide [disabled]")) return;
    inspector.querySelectorAll(".decide button").forEach((b) => { b.disabled = true; });
    inspector.querySelector(`[data-act="${approve ? "approve" : "deny"}"]`).textContent = approve ? "Approving…" : "Denying…";
    await sleep(reduceMotion ? 0 : 500);
    Object.assign(entry, approve
      ? { status: "ok", label: "Approved", decision: "approved", fresh: true }
      : { status: "err", label: "Denied", decision: "denied", fresh: true });
    render();
  }

  function setPlaying(on) {
    playing = on;
    chips.forEach((c) => { c.disabled = on; });
    playButton.disabled = on;
    playButton.querySelector("[data-play-label]").textContent = on ? "Playing…" : "▶︎ Play";
    playButton.querySelector(".long").hidden = on;
  }

  async function play() {
    if (playing) return;
    reset(true);
    setPlaying(true);
    await sleep(reduceMotion ? 0 : 600);
    for (const key of ["patients", "notes", "drop", "cancel"]) {
      const chip = demo.querySelector(`[data-run="${key}"]`);
      chips.forEach((c) => c.classList.toggle("active", c === chip));
      await run(key);
      await sleep(HOLD);
    }
    chips.forEach((c) => c.classList.remove("active"));
    setPlaying(false);
  }

  demo.addEventListener("click", (event) => {
    const target = event.target.closest("button, tbody tr");
    if (!target) return;
    if (target.hasAttribute("data-play")) { mode = "result"; play(); }
    else if (playing) return;
    else if (target.dataset.mode) { mode = target.dataset.mode; render(); }
    else if (target.dataset.rule) {
      policy[target.dataset.rule] = !policy[target.dataset.rule];
      if (window.matchMedia("(max-width: 900px)").matches) mode = "result";
      run(RULES.find((r) => r.key === target.dataset.rule).run);
    }
    else if (target.hasAttribute("data-yaml")) { showYaml = !showYaml; render(); }
    else if (target.hasAttribute("data-defaults")) { policy = { ...DEFAULTS }; render(); }
    else if (target.dataset.run) { mode = "result"; run(target.dataset.run); }
    else if (target.dataset.act) decide(target.dataset.act === "approve");
    else if (target.dataset.id) { selectedId = Number(target.dataset.id); mode = "result"; render(); }
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
