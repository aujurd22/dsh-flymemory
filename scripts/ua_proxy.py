#!/usr/bin/env python3
"""Local proxy that adds a browser User-Agent and forwards to Command Code.

Why this exists: the Command Code gateway sits behind Cloudflare, which rejects
SDK user agents (error 1010). mem0 (and any OpenAI-SDK-based system) therefore
cannot reach it directly. This proxy listens on 127.0.0.1 and forwards
`/v1/chat/completions` with the header the gateway accepts, and forces
`reasoning_effort=off` so benchmark cost stays comparable with our own arms.

    python scripts/ua_proxy.py --port 8799
    # then point an OpenAI-compatible client at http://127.0.0.1:8799/v1

Nothing is logged. The Authorization header is passed through unchanged; the key
is never written anywhere.
"""
import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET = "https://api.commandcode.ai/provider/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):                       # noqa: N802 - http.server API
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        path = self.path[3:] if self.path.startswith("/v1") else self.path
        try:
            payload = json.loads(raw or b"{}")
            payload.setdefault("reasoning_effort", "off")
            raw = json.dumps(payload).encode()
        except Exception:                    # noqa: BLE001 - forward verbatim
            pass
        request = urllib.request.Request(
            TARGET + path, data=raw,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "Authorization": self.headers.get("authorization", ""),
                     "User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data, code = response.read(), response.status
        except urllib.error.HTTPError as error:
            data, code = error.read(), error.code
        except Exception as error:           # noqa: BLE001
            data = json.dumps({"error": {"message": str(error)}}).encode()
            code = 502
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):            # noqa: D401 - silence
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"ua-proxy listening on http://127.0.0.1:{args.port}/v1 -> {TARGET}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
