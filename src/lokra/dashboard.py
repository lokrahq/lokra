"""A local, read-only web dashboard for the audit ledger.

`lokra dashboard` serves a single page on 127.0.0.1 that shows the same activity
and risk report as `lokra report`, refreshed live. It reads the local ledger and
talks to nothing else.
"""
from __future__ import annotations

import http.server
import json
import os
from functools import partial
from importlib import resources

from .config import Config, load_config
from .ledger import Ledger
from .report import build_report, to_dict


def _detail(e: dict) -> str:
    ev = e.get("event")
    if ev == "read":
        return f"{e.get('rows', 0)} rows, {sum((e.get('masked') or {}).values())} masked"
    if ev in ("rejected",):
        return e.get("reason", "")
    if ev == "db_refused":
        return e.get("error", "")
    if ev == "write_proposed":
        return f"{e.get('would_affect_rows', 0)} rows would change"
    if ev == "write_executed":
        return f"{e.get('affected_rows', 0)} rows changed"
    if ev in ("write_approved", "write_denied"):
        return f"by {e.get('decided_by', '-')}"
    if ev == "denied":
        return e.get("reason", "")
    if ev == "listed_tables":
        return ", ".join(e.get("tables") or [])
    return ""


def api_payload(cfg: Config) -> dict:
    led = Ledger(cfg.ledger_path, cfg.signing_key())
    chain_ok = led.verify()[0]
    entries = led.entries()
    recent = [{"ts": (e.get("ts") or "")[11:19], "seq": e.get("seq"),
               "event": e.get("event"), "agent": e.get("agent") or "—",
               "detail": _detail(e)} for e in entries[-40:]][::-1]
    return {"report": to_dict(build_report(entries), chain_ok), "recent": recent}


def make_handler(cfg: Config):
    html = resources.files("lokra").joinpath("dashboard.html").read_text()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(html.encode(), "text/html; charset=utf-8")
            elif path == "/api/report":
                self._send(json.dumps(api_payload(cfg), default=str).encode(), "application/json")
            elif path.startswith("/assets/") and path.endswith(".woff2") and "/" not in path[8:]:
                try:
                    data = resources.files("lokra.assets").joinpath(path[8:]).read_bytes()
                    self._send(data, "font/woff2")
                except (FileNotFoundError, ModuleNotFoundError):
                    self._send(b"not found", "text/plain", 404)
            else:
                self._send(b"not found", "text/plain", 404)

    return Handler


def serve(cfg: Config, port: int) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg))
    print(f"Lokra dashboard on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
