const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const ALLOWED_HOSTS = ["lokra.dev", "www.lokra.dev"];

function allowedOrigin(request) {
  const origin = request.headers.get("Origin");
  if (!origin) return true;
  try {
    const host = new URL(origin).hostname;
    return ALLOWED_HOSTS.includes(host) || host.endsWith(".lokra.pages.dev") || host === "lokra.pages.dev";
  } catch {
    return false;
  }
}

function reply(request, status, body) {
  const wantsJson = (request.headers.get("Accept") || "").includes("application/json");
  if (wantsJson) {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }
  if (status === 200) return Response.redirect(new URL("/joined", request.url).toString(), 303);
  return new Response(body.error || "Bad request", { status, headers: { "Content-Type": "text/plain" } });
}

async function readBody(request) {
  const type = request.headers.get("Content-Type") || "";
  if (type.includes("application/json")) return request.json();
  if (type.includes("form")) return Object.fromEntries(await request.formData());
  return {};
}

export async function onRequestPost({ request, env }) {
  if (!allowedOrigin(request)) return reply(request, 403, { error: "Forbidden." });

  let body;
  try {
    body = await readBody(request);
  } catch {
    return reply(request, 400, { error: "Invalid request." });
  }

  if (body.website) return reply(request, 200, { ok: true });

  const email = String(body.email || "").trim().toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) {
    return reply(request, 400, { error: "That email doesn't look right." });
  }

  try {
    await env.DB.prepare(
      "CREATE TABLE IF NOT EXISTS waitlist (email TEXT PRIMARY KEY, created_at TEXT NOT NULL, country TEXT)"
    ).run();
    await env.DB.prepare("INSERT OR IGNORE INTO waitlist (email, created_at, country) VALUES (?, ?, ?)")
      .bind(email, new Date().toISOString(), request.cf?.country || null)
      .run();
  } catch {
    return reply(request, 500, { error: "Something went wrong. Try again in a moment." });
  }

  return reply(request, 200, { ok: true });
}
