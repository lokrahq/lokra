(() => {
  document.documentElement.classList.add("js");

  const side = document.getElementById("docs-side");
  const menu = document.querySelector("[data-menu]");
  if (side && menu) {
    const setMenu = (open) => {
      side.classList.toggle("open", open);
      document.documentElement.classList.toggle("menu-open", open);
      menu.setAttribute("aria-expanded", String(open));
      if (open) centre();
    };
    const centre = () => {
      const here = side.querySelector("a.on");
      if (here && side.scrollHeight > side.clientHeight) side.scrollTop = here.offsetTop - side.clientHeight / 2;
    };
    menu.addEventListener("click", () => setMenu(!side.classList.contains("open")));
    side.addEventListener("click", (event) => { if (event.target.closest("a")) setMenu(false); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && side.classList.contains("open")) setMenu(false);
    });
    matchMedia("(min-width: 861px)").addEventListener("change", (event) => { if (event.matches) setMenu(false); });
    centre();
  }

  const icons = {
    copy: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/>'
      + '<path d="M10.5 5.5V4A1.5 1.5 0 0 0 9 2.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5"/></svg>',
    done: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7"/></svg>',
  };
  for (const host of document.querySelectorAll(".docs-body .hl")) {
    const head = host.querySelector(".code-head");
    const pre = host.querySelector("pre");
    if (!head || !pre) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy";
    const show = (icon, label) => { button.innerHTML = `${icons[icon]}<span>${label}</span>`; };
    show("copy", "Copy");
    button.addEventListener("click", async () => {
      const text = pre.innerText.replace(/\n$/, "");
      try {
        await navigator.clipboard.writeText(text);
        show("done", "Copied");
        button.classList.add("done");
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        show("copy", "Press ⌘C");
      }
      setTimeout(() => { show("copy", "Copy"); button.classList.remove("done"); }, 1600);
    });
    head.appendChild(button);
  }

  const tocLinks = [...document.querySelectorAll(".docs-toc a")];
  if (tocLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(tocLinks.map((a) => [decodeURIComponent(a.getAttribute("href").slice(1)), a]));
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        tocLinks.forEach((a) => a.classList.remove("on"));
        byId.get(entry.target.id)?.classList.add("on");
      }
    }, { rootMargin: `-${(document.querySelector(".docs-header")?.offsetHeight || 0) + 8}px 0px -70% 0px` });
    for (const id of byId.keys()) {
      const heading = document.getElementById(id);
      if (heading) observer.observe(heading);
    }
  }

  const input = document.querySelector("[data-search]");
  const box = document.querySelector("[data-results]");
  if (!input || !box) return;
  let index = null;
  let active = 0;
  const load = async () => {
    if (!index) index = await (await fetch("/docs/search.json")).json();
    return index;
  };
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const score = (entry, terms) => {
    const title = entry.t.toLowerCase();
    const section = entry.s.toLowerCase();
    const text = entry.x.toLowerCase();
    let total = 0;
    for (const term of terms) {
      if (title.includes(term)) total += 10;
      else if (section.includes(term)) total += 4;
      else if (text.includes(term)) total += 2;
      else return 0;
    }
    return total + (entry.u.includes("#") ? 0 : 3);
  };
  const paint = () => box.querySelectorAll("a").forEach((a, i) => a.classList.toggle("on", i === active));
  const run = async () => {
    const query = input.value.trim().toLowerCase();
    if (!query) { box.hidden = true; return; }
    const terms = query.split(/\s+/);
    const hits = (await load()).map((e) => [score(e, terms), e]).filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0]).slice(0, 8).map(([, e]) => e);
    active = 0;
    box.innerHTML = hits.length
      ? hits.map((e) => `<a href="${esc(e.u)}"><b>${esc(e.t)}</b><small>${esc(e.s)}</small>${e.x ? `<span>${esc(e.x)}</span>` : ""}</a>`).join("")
      : '<div class="none">No matches. Try fewer or different words.</div>';
    paint();
    box.hidden = false;
  };
  input.addEventListener("input", run);
  input.addEventListener("focus", () => { load(); if (input.value) run(); });
  input.addEventListener("keydown", (event) => {
    const items = [...box.querySelectorAll("a")];
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!items.length) return;
      active = (active + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      paint();
      items[active].scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter" && items[active]) {
      location.href = items[active].href;
    } else if (event.key === "Escape") {
      box.hidden = true;
      input.blur();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !/^(input|textarea|select)$/i.test(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".docs-search")) box.hidden = true;
  });
})();
