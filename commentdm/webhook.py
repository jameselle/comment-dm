"""Optional: receive events the moment they happen instead of polling. Meta sends them to a Live app at a
public HTTPS address (a tunnel such as Cloudflare Tunnel or Tailscale Funnel in front of this Mac), for
accounts subscribed to the app (`check` subscribes yours). Until then, `run` polls. Not yet tried live.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, Tuple
from urllib.parse import parse_qs, urlparse


def signature_ok(app_secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def events(payload: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Turn a webhook body into ("comment", {...}) and ("message", {...}) in the engine's shapes."""
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            if ch.get("field") == "comments":
                v = ch.get("value", {})
                frm = v.get("from", {})
                yield "comment", {"id": v.get("id"), "text": v.get("text"), "at": float(entry.get("time", 0)),
                                  "user_id": str(frm.get("id", "")), "username": frm.get("username"),
                                  "media_id": (v.get("media") or {}).get("id")}
        for m in entry.get("messaging", []):
            msg = m.get("message") or {}
            if not msg or msg.get("is_echo"):
                continue
            yield "message", {"id": msg.get("mid"), "from_id": str((m.get("sender") or {}).get("id", "")), "username": None,
                              "text": msg.get("text"), "at": float(m.get("timestamp", 0)) / 1000}


def serve(engine: Any, app_secret: str, verify_token: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            q = parse_qs(urlparse(self.path).query)
            if q.get("hub.mode", [""])[0] == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", [""])[0], verify_token):
                self._reply(200, q.get("hub.challenge", [""])[0].encode())
            else:
                self._reply(403, b"forbidden")

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not signature_ok(app_secret, body, self.headers.get("X-Hub-Signature-256", "")):
                return self._reply(403, b"bad signature")
            self._reply(200, b"ok")  # answer Meta fast; then act
            for kind, ev in events(json.loads(body or b"{}")):
                if ev.get("id"):
                    engine.on_comment(ev) if kind == "comment" else engine.on_message(ev)

        def _reply(self, code: int, data: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    print(f"webhook listening on 127.0.0.1:{port} (put a public HTTPS tunnel in front of it)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
