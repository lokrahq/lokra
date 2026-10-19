document.documentElement.classList.add("js");

document.querySelectorAll("[data-copy-link]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copyLink);
      button.textContent = "Copied ✓";
    } catch {
      button.textContent = "lokra.dev";
    }
    setTimeout(() => { button.textContent = "Copy link"; }, 2000);
  });
});

const announce = document.querySelector("[data-announce]");
if (announce) {
  try { if (localStorage.getItem("lokra-announce-hidden") === "1") announce.hidden = true; } catch {}
  announce.querySelector("[data-dismiss]").addEventListener("click", () => {
    announce.hidden = true;
    try { localStorage.setItem("lokra-announce-hidden", "1"); } catch {}
  });
}

document.querySelectorAll(".waitlist").forEach((form) => {
  const status = form.querySelector(".status");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = form.email.value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      if (status) status.textContent = "That email doesn't look right.";
      form.email.focus();
      return;
    }
    const button = form.querySelector("button");
    button.disabled = true;
    if (status) status.textContent = "";
    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email, website: form.website.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Something went wrong.");
      window.location.href = "/joined";
    } catch (err) {
      if (status) status.textContent = err.message || "Something went wrong. Try again in a moment.";
      button.disabled = false;
    }
  });
  form.email.addEventListener("input", () => { if (status) status.textContent = ""; });
});

const shield = document.querySelector("[data-shield]");

if (shield) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const steps = [...shield.querySelectorAll(".core-steps li")];
  const verdict = shield.querySelector("[data-verdict]");
  const badge = shield.querySelector(".core-badge");
  const tokIn = shield.querySelector('[data-token="in"]');
  const tokOut = shield.querySelector('[data-token="out"]');
  const pg = shield.querySelector(".col-d .dbi");
  const bar = document.querySelector("[data-scn-bar]");
  const buttons = bar ? [...bar.querySelectorAll("[data-scn]")] : [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, reduce ? 0 : ms));

  const SCN = {
    read: { label: "Masked · 12 identifiers", ok: true, skip: [3] },
    write: { label: "Approved · 1 row", ok: true, wait: 3, skip: [5] },
    attack: { label: "Blocked · DROP TABLE", ok: false, blockAt: 2 },
  };
  const ORDER = ["read", "write", "attack"];

  let runId = 0, gen = 0, auto = 0, resumeTimer;

  const fire = (tok, color) => {
    if (!tok || reduce) return;
    tok.className = "tok " + color;
    void tok.offsetWidth;
    tok.classList.add("run");
  };

  async function play(name) {
    const id = ++runId;
    steps.forEach((s) => (s.className = ""));
    badge.classList.remove("flash");
    verdict.textContent = "Checking…";
    verdict.className = "verdict run";
    buttons.forEach((b) => b.classList.toggle("on", b.dataset.scn === name));
    const scn = SCN[name];
    fire(tokIn, scn.ok ? "green" : "red");
    await sleep(620);
    if (id !== runId) return;
    for (let i = 1; i <= steps.length; i++) {
      const li = steps[i - 1];
      if (scn.skip && scn.skip.includes(i)) { li.classList.add("skip"); continue; }
      li.classList.add("on");
      if (!scn.ok && scn.blockAt === i) {
        await sleep(380);
        if (id !== runId) return;
        li.classList.replace("on", "blocked");
        for (let j = i + 1; j <= steps.length; j++) steps[j - 1].classList.add("skip");
        badge.classList.add("flash");
        verdict.textContent = scn.label;
        verdict.className = "verdict err";
        return;
      }
      if (scn.wait === i) { li.classList.add("wait"); await sleep(820); if (id !== runId) return; li.classList.remove("wait"); }
      await sleep(400);
      if (id !== runId) return;
      li.classList.replace("on", "done");
    }
    fire(tokOut, "green");
    if (pg) { pg.classList.add("pgpulse"); setTimeout(() => pg.classList.remove("pgpulse"), 520); }
    verdict.textContent = scn.label;
    verdict.className = "verdict";
  }

  async function autoLoop() {
    const g = ++gen;
    while (g === gen) {
      await play(ORDER[auto++ % ORDER.length]);
      if (g !== gen) return;
      await sleep(3600);
    }
  }

  buttons.forEach((b) => b.addEventListener("click", () => {
    gen++;
    clearTimeout(resumeTimer);
    auto = ORDER.indexOf(b.dataset.scn) + 1;
    play(b.dataset.scn);
    resumeTimer = setTimeout(autoLoop, 8000);
  }));

  if (reduce) {
    steps.forEach((s, i) => s.classList.add(i === 2 ? "skip" : "done"));
    verdict.textContent = SCN.read.label;
  } else if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((e) => {
      if (e.some((x) => x.isIntersecting)) { io.disconnect(); autoLoop(); }
    }, { threshold: 0.4 });
    io.observe(shield);
  } else {
    autoLoop();
  }
}
