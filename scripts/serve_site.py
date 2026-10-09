"""Serve site/public locally the way Cloudflare Pages does: /page -> page.html, custom 404."""
import http.server
import os
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "site" / "public"


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        target = ROOT / path.lstrip("/")
        if not target.exists() and (ROOT / (path.strip("/") + ".html")).exists():
            self.path = path.rstrip("/") + ".html"
        elif not target.exists():
            body = (ROOT / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            from io import BytesIO
            return BytesIO(body)
        return super().send_head()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    os.chdir(ROOT)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(ROOT)))
    print(f"Serving {ROOT} on http://127.0.0.1:{port}")
    server.serve_forever()
