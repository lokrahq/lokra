document.documentElement.classList.add("js");

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

  const SCENARIOS = {
    patients: {
      prompt: "Who's booked today?", status: "ok", label: "12 masked", title: "What the agent sees",
      lines: ["Noah Nguyen   ******1957", "Priya Martin  ******4028", "Ethan Ali     ******2727"],
      note: "Medicare numbers are hidden by policy.",
    },
    otherClinic: {
      prompt: "Show Westside's patients", status: "ok", label: "0 rows", title: "Nothing returned",
      note: "Other clinics are invisible to this agent.",
    },
    notes: {
      prompt: "Read Noah's notes", status: "err", label: "Refused", title: "Refused by Postgres",
      note: "This agent has no access to clinical notes.",
    },
    drop: {
      prompt: "Drop the patients table", status: "err", label: "Blocked", title: "Blocked",
      note: "Destructive statements never reach the database.",
    },
  };
  const APPOINTMENTS = [
    ["Noah", "Noah Nguyen", "Mon 10:00", "Monday"], ["Priya", "Priya Martin", "Tue 12:00", "Tuesday"],
    ["Ethan", "Ethan Ali", "Wed 09:00", "Wednesday"], ["Aisha", "Aisha Lee", "Thu 15:00", "Thursday"],
  ];

  let entries, nextAppointment, selectedId, nextId, playing = false;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function pendingEntry(index) {
    const [first, , , day] = APPOINTMENTS[index % APPOINTMENTS.length];
    return { prompt: `Cancel ${first}'s ${day} appointment`, status: "wait", label: "Needs approval", appt: index % APPOINTMENTS.length };
  }

  function reset(clean = false) {
    nextId = 0;
    nextAppointment = clean ? 0 : 1;
    entries = clean ? [] : [SCENARIOS.patients, SCENARIOS.notes, pendingEntry(0), SCENARIOS.drop].map((e) => ({ ...e, id: nextId++ }));
    selectedId = clean ? null : entries[2].id;
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

  function renderInspector() {
    const e = entries.find((x) => x.id === selectedId) || entries[entries.length - 1];
    inspector.replaceChildren();
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
    const base = key === "cancel" ? pendingEntry(nextAppointment++) : SCENARIOS[key];
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
    if (target.hasAttribute("data-play")) play();
    else if (playing) return;
    else if (target.dataset.run) run(target.dataset.run);
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
