document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy"; }, 1600);
    } catch {
      button.textContent = "Press ⌘C";
    }
  });
});

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
